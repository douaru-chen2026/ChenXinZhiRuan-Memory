# -*- coding: utf-8 -*-
"""Cross-Encoder 重排序（Rerank）：在混合检索召回的候选池上做精细相关性打分。

为什么还需要 Rerank：
  混合检索（hybrid.py）里的 BM25 和向量相似度，都是“字面 / 整体语义”的粗排，
  两条文本分别编码、再比距离，速度快但不懂“这句话在此时此刻对用户的意义”。
  Cross-Encoder 把【问题】和【候选】拼在一起，一次性过一遍模型，
  能看到词与词之间的交叉关系，打分更准——代价是每个候选都要算一次，所以只
  用在“先粗排召回的小候选池（默认 50 条）”上，而不是全库。

设计：
  - 模型懒加载、进程内单例，不每次重新加载；
  - 核心档案（layer 0）在 recall_all 里强制置顶、不进本模块精排；
  - 模型加载 / 推理任何异常都自动降级，返回候选原顺序，绝不中断主流程；
  - 纯 CPU 运行，限制线程数与序列长度，控制“开口前”的等待时间。

用法：
  python3 -m chenxin.rerank "她爱喝什么"
"""
import argparse
import time

MODEL_NAME = "BAAI/bge-reranker-base"
DEFAULT_POOL = 50        # 粗排候选池大小：先召回多少条再精排
DEFAULT_MAXLEN = 256     # 单对文本最大 token，截断控制 CPU 耗时
DEFAULT_BATCH = 16       # CPU 推理批大小

def _score_pairs(query, docs, max_length, batch_size):
    """对一组 (query, doc) 对计算相关性 logit，返回与 docs 等长的分数列表。

    走轻量 ONNX 后端（onnxruntime），不 import torch/transformers，
    模型在 onnx_backend 里进程内单例懒加载。
    """
    from . import onnx_backend

    reranker = onnx_backend.get_reranker()
    return reranker.score_pairs(
        query, docs, max_length=max_length, batch_size=batch_size
    )


def rerank(query, candidates, top_n, max_length=DEFAULT_MAXLEN,
           batch_size=DEFAULT_BATCH):
    """对候选池精排，返回 (top_results, meta)。

    candidates：hybrid 粗排后的候选列表，每条至少含 content；
    成功时给每条写入 rerank_score 并按其降序取 top_n；
    任何异常都降级为“按候选原顺序取 top_n”，保证召回不被阻断。
    """
    started = time.time()
    docs = [c.get("content", "") or "" for c in candidates]

    try:
        if not docs:
            return [], {"mode": "rerank", "pool": 0, "elapsed": 0.0}
        scores = _score_pairs(query, docs, max_length, batch_size)
        for cand, score in zip(candidates, scores):
            cand["rerank_score"] = round(score, 5)
        ranked = sorted(candidates, key=lambda x: -x["rerank_score"])[:top_n]
        meta = {
            "mode": "rerank",
            "model": MODEL_NAME,
            "pool": len(candidates),
            "elapsed": round(time.time() - started, 2),
        }
        return ranked, meta

    except Exception as exc:  # 模型/推理失败：降级为粗排原顺序
        meta = {
            "mode": "degraded",
            "reason": str(exc)[:120],
            "pool": len(candidates),
            "elapsed": round(time.time() - started, 2),
        }
        return list(candidates)[:top_n], meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--pool", type=int, default=DEFAULT_POOL)
    ap.add_argument("--top", type=int, default=6)
    args = ap.parse_args()

    # 直接用混合检索造候选池，再精排，便于单独验证
    from . import hybrid

    candidates, hmeta = hybrid.search(args.query, top=args.pool)
    results, meta = rerank(args.query, candidates, top_n=args.top)

    if meta["mode"] == "rerank":
        print(f"【Cross-Encoder 精排】{args.query} "
              f"(候选池{meta['pool']} 耗时{meta['elapsed']}s)")
    else:
        print(f"【精排降级·保持粗排】{meta.get('reason', '')}")
    for i, r in enumerate(results, 1):
        who = "user" if r.get("role") == "user" else "assistant"
        ts = (r.get("ts") or "")[:19].replace("T", " ")
        print(f"\n[{i}] {ts} {who} | 精排分{r.get('rerank_score', '-')}")
        print(r.get("content", "")[:280])


if __name__ == "__main__":
    main()

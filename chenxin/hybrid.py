# -*- coding: utf-8 -*-
"""原话层混合检索：FTS 关键词（BM25）+ BGE 语义，RRF 融合。

为什么用 RRF（Reciprocal Rank Fusion）：
  FTS 给的是 BM25 负值、语义给的是 cosine distance，两套分数量度不同、
  无法直接相加。RRF 只看每条结果在各自榜单里的“名次”，
  score = Σ 1/(k + rank)，对量度不敏感、几乎不用调参，是工业界混合检索的稳妥做法。

特性：
  - 按 id 去重，同一条被两路都命中时融合其排名分；
  - 语义层/模型加载失败时自动降级为 FTS-only，不报错、不中断；
  - 输出统一结构，标注每条来自哪一路、是否降级。

用法：
  python3 -m chenxin.hybrid search "换种说法的问题" --top 6
"""
import argparse
import sys

from . import raw_fts, raw_semantic

RRF_K = 60  # RRF 常数，越大则名次差异越平滑（标准取值 60）


def _semantic_hits(query, top):
    """把 chroma 语义结果解析成有序列表，返回 [(id, role, ts, content, sim)]。"""
    res = raw_semantic.search(query, top=top)
    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    hits = []
    for id_, doc, meta, dist in zip(ids, docs, metas, dists):
        meta = meta or {}
        sim = 1 - dist  # cosine distance -> 相似度
        hits.append((id_, meta.get("role", ""), meta.get("ts", ""), doc, sim))
    return hits


def search(query, top=6, rrf_k=RRF_K):
    """混合检索原话，返回 (results, meta)。

    results 每条：
      {id, role, ts, content, fts_rank, sem_rank, score, sources}
    meta：{"mode": "hybrid"|"fts_only", "fts_count", "sem_count"}
    """
    # 关键词路（多取一些，保证融合候选充足）
    fts_rows = raw_fts.search(query, top=top)
    if isinstance(fts_rows, dict):  # FTS 出错
        fts_rows = []
    fts_rank = {r["id"]: i + 1 for i, r in enumerate(fts_rows)}

    # 语义路（带容错）
    degraded = False
    try:
        sem_hits = _semantic_hits(query, top)
    except Exception as exc:  # 模型/库不可用，降级
        sem_hits = []
        degraded = True
        sem_error = str(exc)
    sem_rank = {h[0]: i + 1 for i, h in enumerate(sem_hits)}

    # 汇总候选（按 id 去重）
    pool = {}
    for r in fts_rows:
        pool[r["id"]] = {
            "id": r["id"], "role": r["role"], "ts": r["ts"],
            "content": r["content"],
        }
    for id_, role, ts, content, _sim in sem_hits:
        if id_ not in pool:
            pool[id_] = {"id": id_, "role": role, "ts": ts, "content": content}

    # RRF 融合打分
    for id_, item in pool.items():
        score = 0.0
        sources = []
        fr = fts_rank.get(id_)
        sr = sem_rank.get(id_)
        if fr is not None:
            score += 1.0 / (rrf_k + fr)
            sources.append("fts")
        if sr is not None:
            score += 1.0 / (rrf_k + sr)
            sources.append("semantic")
        item["fts_rank"] = fr
        item["sem_rank"] = sr
        item["score"] = round(score, 6)
        item["sources"] = sources

    results = sorted(pool.values(), key=lambda x: -x["score"])[:top]
    mode = "fts_only" if degraded or not sem_hits else "hybrid"
    meta = {"mode": mode, "fts_count": len(fts_rows), "sem_count": len(sem_hits)}
    if degraded:
        meta["sem_error"] = sem_error
    return results, meta


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--top", type=int, default=6)
    args = ap.parse_args()

    results, meta = search(args.query, top=args.top)
    tag = "混合" if meta["mode"] == "hybrid" else "降级·仅关键词"
    print(f"【混合检索·{tag}】{args.query} "
          f"(关键词{meta['fts_count']} 语义{meta['sem_count']})")
    if meta.get("sem_error"):
        print(f"〔语义层不可用已自动降级：{meta['sem_error'][:80]}〕")
    for i, r in enumerate(results, 1):
        who = "user" if r["role"] == "user" else "assistant"
        ts = (r["ts"] or "")[:19].replace("T", " ")
        src = "+".join(r["sources"])
        print(f"\n[{i}] {ts} {who} | 来源:{src} | 融合分{r['score']:.5f}")
        print(r["content"][:300])


if __name__ == "__main__":
    main()

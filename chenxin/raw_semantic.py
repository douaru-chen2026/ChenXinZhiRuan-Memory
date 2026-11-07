# -*- coding: utf-8 -*-
"""原话层 BGE 语义索引：让"换种说法、比喻、没明说"的记忆也能召回。

对 raw_events 全量原话用 bge-small-zh-v1.5 编码，存入 Chroma：
    build   建/重建语义索引（CPU 较慢，带进度，可后台跑）
    search  自然语言语义查询

用法：
    python3 -m chenxin.raw_semantic build
    python3 -m chenxin.raw_semantic search "她爱喝什么" --top 5
"""
import argparse
import sys
import time
from pathlib import Path

from . import config

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
SEM_DIR = Path(config.RUNTIME_DIR) / "semantic"
BATCH = 128


def _model():
    from . import onnx_backend
    return onnx_backend.get_encoder()


def _client():
    import chromadb
    SEM_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(SEM_DIR))


def _collection(client):
    return client.get_or_create_collection("raw_semantic")


def build(rebuild=False):
    """建语义索引。默认增量：不删库，只补库里还没有的 id，可反复跑、不重复算。
    rebuild=True 时才删库做全量重建。"""
    import sqlite3
    conn = sqlite3.connect(config.PIPELINE_DB)
    rows = conn.execute(
        "SELECT id, role, content, ts FROM raw_events ORDER BY seq").fetchall()
    conn.close()

    model = _model()
    client = _client()
    if rebuild:
        try:
            client.delete_collection("raw_semantic")
        except Exception:
            pass
    col = _collection(client)

    # 库里已有的 id（只取 id，不拉向量和文档）
    try:
        existing = set(col.get(include=[])["ids"])
    except Exception:
        existing = set()
    pending = [r for r in rows if r[0] not in existing]
    skipped = len(rows) - len(pending)
    print(f"  全量 {len(rows)}，已索引 {skipped}，待补 {len(pending)}",
          flush=True)
    if not pending:
        print("  无新增，语义索引已是最新", flush=True)
        return len(rows)

    total = len(pending)
    start = time.time()
    for i in range(0, total, BATCH):
        chunk = pending[i:i + BATCH]
        texts = [r[2] for r in chunk]
        emb = model.encode(texts)
        col.add(
            ids=[r[0] for r in chunk],
            documents=texts,
            embeddings=[e.tolist() for e in emb],
            metadatas=[{"role": r[1] or "", "ts": r[3] or ""} for r in chunk],
        )
        done = min(i + BATCH, total)
        elapsed = time.time() - start
        rate = done / max(elapsed, 1)
        eta = (total - done) / max(rate, 1)
        print(f"  本次新增 {done}/{total}  {rate:.0f}条/秒  ETA {eta/60:.1f}分",
              flush=True)
    return len(rows)


def search(query, top=5):
    """语义查询。"""
    model = _model()
    emb = model.encode([query])[0].tolist()
    res = _collection(_client()).query(query_embeddings=[emb], n_results=top)
    return res


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    bp = sub.add_parser("build")
    bp.add_argument("--rebuild", action="store_true",
                    help="删库全量重建（默认只做增量、只补新增）")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    if args.cmd == "build":
        print(f"语义索引完成，共 {build(rebuild=args.rebuild)} 条")
    else:
        res = search(args.query, args.top)
        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        for i, (id_, doc, meta, dist) in enumerate(zip(ids, docs, metas, dists), 1):
            who = "user" if meta.get("role") == "user" else "assistant"
            ts = (meta.get("ts") or "")[:19].replace("T", " ")
            print(f"\n[{i}] {ts} {who} (相似度{1-dist:.3f}):\n{doc}")


if __name__ == "__main__":
    main()

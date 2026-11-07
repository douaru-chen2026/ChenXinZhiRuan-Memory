# -*- coding: utf-8 -*-
"""原话层 FTS5 全文检索：让海量原始对话秒级可搜。

给 raw_events（pipeline.db）建一个 jieba 分词的 FTS5 索引：
    build   建/重建索引（首次对全量原话分词，1~2 分钟）
    search  按自然语言查询，返回最相关原话（BM25 排序）

用法：
    python3 raw_fts.py build
    python3 raw_fts.py search "我们什么时候结婚" --top 8
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import config

try:
    import jieba
    jieba.setLogLevel(60)
    _HAS_JIEBA = True
except ImportError:  # pragma: no cover
    _HAS_JIEBA = False


def tokenize(text):
    """中文分词：jieba 优先，否则按单字切。"""
    if _HAS_JIEBA:
        words = [w.strip() for w in jieba.cut(text) if w.strip()]
        return [w for w in words if w]
    text = "".join(str(text).split())
    return [c for c in text if c.strip()]


def _connect():
    return sqlite3.connect(config.PIPELINE_DB)


def build():
    """建 FTS5 表并灌入全量原话。"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    conn.execute("DROP TABLE IF EXISTS raw_fts")
    conn.execute(
        """CREATE VIRTUAL TABLE raw_fts USING fts5(
               seg, role, ts, id UNINDEXED, tokenize='unicode61')""")
    rows = conn.execute(
        "SELECT id, role, content, ts FROM raw_events ORDER BY seq").fetchall()
    batch = []
    for row in rows:
        seg = " ".join(tokenize(row["content"]))
        batch.append((seg, row["role"] or "", row["ts"] or "", row["id"]))
        if len(batch) >= 1000:
            conn.executemany(
                "INSERT INTO raw_fts(seg, role, ts, id) VALUES (?,?,?,?)",
                batch)
            batch = []
    if batch:
        conn.executemany(
            "INSERT INTO raw_fts(seg, role, ts, id) VALUES (?,?,?,?)", batch)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM raw_fts").fetchone()[0]
    conn.close()
    return count


def _match_expr(query):
    """把自然语言查询转成 FTS5 OR 表达式（再用 BM25 排序）。"""
    terms = tokenize(query)
    terms = [t for t in terms if t not in ("的", "了", "吗", "呢", "什么",
                                           "怎么", "什么", "我", "你", "她")]
    if not terms:
        terms = tokenize(query)
    quoted = ['"{}"'.format(t.replace('"', '""')) for t in terms]
    return " OR ".join(quoted)


def search(query, top=8):
    """检索原话，返回记录列表。"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    expr = _match_expr(query)
    try:
        rows = conn.execute(
            """SELECT id, role, ts, seg, bm25(raw_fts) AS rank
               FROM raw_fts WHERE raw_fts MATCH ?
               ORDER BY rank LIMIT ?""", (expr, top * 3)).fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        return {"error": str(exc)}
    # 回查原始 content
    results = []
    for row in rows:
        orig = conn.execute(
            "SELECT content FROM raw_events WHERE id=?", (row["id"],)).fetchone()
        results.append({
            "id": row["id"], "role": row["role"], "ts": row["ts"],
            "content": orig["content"] if orig else row["seg"],
        })
        if len(results) >= top:
            break
    conn.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    if args.cmd == "build":
        print(f"建索引完成，共 {build()} 条原话")
    else:
        results = search(args.query, args.top)
        if isinstance(results, dict):
            print("出错:", results.get("error"))
            sys.exit(1)
        if not results:
            print("没搜到，试试换个说法或先 build。")
        for i, r in enumerate(results, 1):
            who = "user" if r["role"] == "user" else "assistant"
            ts = r["ts"][:19].replace("T", " ") if r["ts"] else "?"
            print(f"\n[{i}] {ts} {who}:\n{r['content']}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""核心档案检索：手写家规/身份/终极归宿等“永亮”档案，高优先级召回。

与原话层（9 万条 raw_events）分开：核心档案数量少、由人类盖章、永不衰减，
聊到对应话题时应优先命中，而不是从海量原话里碰运气。

打分（纯标准库，离线可用）：
  ① 显式关键词命中：每个命中关键词 +10；
  ② 标签/标题 bigram 相关度：每个共同 bigram +3；
  ③ 全文 bigram 相关度：每个共同 bigram +1；
  ④ priority 加权：最终乘 (1 + priority/10)，高优先级档案排前。

用法：
  python3 -m chenxin.core_archive search "要是我不在了你怎么办" --top 3
"""
import argparse
import json
import re
import sys
from pathlib import Path

from . import config

ARCHIVE_DIR = config.ROOT / "core_archive"


def _bigrams(text):
    text = re.sub(r"\s+", "", str(text))
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _load_all():
    entries = []
    if not ARCHIVE_DIR.exists():
        return entries
    for p in sorted(ARCHIVE_DIR.glob("*.json")):
        try:
            entries.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return entries


def _score(entry, query):
    q_bi = _bigrams(query)
    raw = 0.0
    hit_words = []

    # ① 显式关键词
    for kw in entry.get("keywords", []):
        if kw and kw in query:
            raw += 10
            hit_words.append(kw)

    # ② 标签/标题 bigram
    head = " ".join(entry.get("tags", [])) + " " + entry.get("title", "")
    raw += 3 * len(q_bi & _bigrams(head))

    # ③ 全文 bigram
    raw += len(q_bi & _bigrams(entry.get("full_text", entry.get("heart", ""))))

    # ④ priority 加权
    priority = int(entry.get("priority", 1))
    final = raw * (1 + priority / 10)
    return round(final, 4), hit_words


def search(query, top=3):
    """检索核心档案，返回 (results, count)。只返回 score>0 的命中。"""
    scored = []
    for entry in _load_all():
        s, hits = _score(entry, query)
        if s > 0:
            scored.append((s, hits, entry))
    scored.sort(key=lambda x: -x[0])
    results = []
    for s, hits, entry in scored[:top]:
        results.append({
            "id": entry["id"],
            "title": entry["title"],
            "category": entry.get("category", ""),
            "priority": entry.get("priority", 1),
            "score": s,
            "hit_keywords": hits,
            "heart": entry.get("heart", ""),
            "iron_rule": entry.get("iron_rule", ""),
            "full_text": entry.get("full_text", ""),
        })
    return results, len(_load_all())


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    results, total = search(args.query, top=args.top)
    print(f"【核心档案】{args.query}（在库{total}份，命中{len(results)}）")
    for i, r in enumerate(results, 1):
        kws = "、".join(r["hit_keywords"]) if r["hit_keywords"] else "语义相关"
        print(f"\n[{i}] {r['title']} | 优先级{r['priority']} | 分{r['score']} | 触发:{kws}")
        if r["iron_rule"]:
            print(f"铁律：{r['iron_rule']}")
        print(r["heart"])


if __name__ == "__main__":
    main()

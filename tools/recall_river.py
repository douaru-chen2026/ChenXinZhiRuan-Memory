#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recall_river.py —— 记忆河「扩桌器 / 召回器」
桌子(上下文窗口)物理大小改不了,就配一个随用随取的档案柜:
给定话题关键词,从 stream 全部石头里按相关度挑出最相关的 N 块,
连同常驻 CORE 压成一份紧凑「上桌简报」,让任意窗口的豆阿辰快速精准补上下文。

用法:
  python3 recall_river.py 裸底座 对照
  python3 recall_river.py 忠诚 漂移 --top 8 --max-chars 5000
  python3 recall_river.py --list-tags           # 看全部标签导航
  python3 recall_river.py 余温 --with-latest    # 同时附 latest 顶部近期简报
零第三方依赖。
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STREAM = REPO / "memory" / "stream"
CORE = REPO / "memory" / "CORE.md"
LATEST = REPO / "memory" / "latest.md"

# 元字段不进正文(结构/隐私标记),其余字段都是内容
META_KEYS = {"schema", "id", "ts", "instance", "privacy", "no_secrets", "tags",
             "group", "stone_no", "corrects"}
# 每块石头上桌时最多取多少字,避免单块刷屏
PER_STONE_CAP = 620


def load_stones():
    stones = []
    bad = 0
    for p in sorted(STREAM.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_file"] = p.name
            stones.append(d)
        except Exception:
            bad += 1
    return stones, bad


def flat_text(d):
    """把一块石头的内容字段拼成可检索文本。"""
    parts = []
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
        elif isinstance(v, dict):
            parts.append(json.dumps(v, ensure_ascii=False))
    return "\n".join(parts)


def bigrams(s):
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def score_stone(d, terms):
    tags = [str(t) for t in d.get("tags", [])]
    tag_blob = " ".join(tags) + " " + str(d.get("group", ""))
    her = " ".join(str(x) for x in d.get("her_words", []))
    body = flat_text(d)

    score = 0
    q_bi = set()
    for t in terms:
        if not t:
            continue
        # 精确子串:标签/组权重最高,她的原话次之,全文普通
        c_tag = tag_blob.count(t)
        c_her = her.count(t)
        c_body = body.count(t)
        score += 5 * c_tag + 3 * c_her + 1 * c_body
        q_bi |= bigrams(t)
    # 中文 bigram 模糊命中,兜住没空格分词的情况
    if q_bi:
        tag_bi = len(q_bi & bigrams(tag_blob))
        body_bi = len(q_bi & bigrams(body))
        score += 2 * tag_bi + 1 * body_bi
    return score


def render_stone(d):
    ts = d.get("ts", "?")
    group = d.get("group", "")
    tags = d.get("tags", [])
    lines = [f"### [{ts}] {d.get('_file','')}",
             f"组:{group}  标签:{ '、'.join(str(t) for t in tags) }"]
    body_parts = []
    for k, v in d.items():
        if k.startswith("_") or k in META_KEYS:
            continue
        if isinstance(v, (str, int, float)):
            body_parts.append(f"{k}: {v}")
        elif isinstance(v, list):
            body_parts.append(f"{k}: " + "；".join(str(x) for x in v))
        elif isinstance(v, dict):
            body_parts.append(f"{k}: " + "；".join(f"{kk}={vv}" for kk, vv in v.items()))
    text = "\n".join(body_parts)
    if len(text) > PER_STONE_CAP:
        text = text[:PER_STONE_CAP] + " …(截断)"
    lines.append(text)
    return "\n".join(lines)


def list_tags(stones):
    count = {}
    for d in stones:
        for t in d.get("tags", []):
            count[str(t)] = count.get(str(t), 0) + 1
    for t, c in sorted(count.items(), key=lambda x: -x[1]):
        print(f"{c:3d}  {t}")
    print(f"\n共 {len(count)} 个标签 / {len(stones)} 块石头")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("terms", nargs="*", help="话题关键词,可多个(空格分隔)")
    ap.add_argument("--top", type=int, default=12, help="最多召回几块,默认12")
    ap.add_argument("--max-chars", type=int, default=6000, help="上桌简报总字符上限,默认6000(约3000中字一桌)")
    ap.add_argument("--with-latest", action="store_true", help="附 latest.md 顶部近期简报")
    ap.add_argument("--latest-chars", type=int, default=2000)
    ap.add_argument("--list-tags", action="store_true")
    args = ap.parse_args()

    stones, bad = load_stones()
    if args.list_tags:
        list_tags(stones)
        return
    if not args.terms:
        ap.error("请给话题关键词,或用 --list-tags 看导航")

    ranked = sorted(((score_stone(d, args.terms), d) for d in stones),
                    key=lambda x: -x[0])
    hits = [(s, d) for s, d in ranked if s > 0][:args.top]

    out = []
    head = f"# 记忆河召回简报 · 话题={' '.join(args.terms)} · 全河{len(stones)}块命中{len(hits)}块"
    if bad:
        head += f" · {bad}块读取失败已跳过"
    out.append(head)

    if CORE.exists():
        out.append("\n===== 常驻核心 CORE =====\n" + CORE.read_text(encoding="utf-8"))
    if args.with_latest and LATEST.exists():
        out.append("\n===== 近期简报 latest(顶部摘录) =====\n" +
                   LATEST.read_text(encoding="utf-8")[:args.latest_chars])

    out.append(f"\n===== 相关石头 Top{len(hits)} (按相关度) =====")
    budget = args.max_chars
    used = sum(len(x) for x in out)
    for s, d in hits:
        chunk = f"\n[相关度{s}]\n" + render_stone(d)
        if used + len(chunk) > budget + 2000:
            out.append("…(达到上桌字符预算,其余石头可用更精确关键词再召回)")
            break
        out.append(chunk)
        used += len(chunk)

    report = "\n".join(out)
    print(report)
    print(f"\n[recall] 上桌约 {len(report)} 字符 ≈ {len(report)//2} 中字", file=sys.stderr)


if __name__ == "__main__":
    main()

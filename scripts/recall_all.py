#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗统一召回（开口前自动跑）：一次查询，核心档案 + 原话混合检索 + 记忆河石头，紧凑输出。

设计为当值主窗每轮回答前的标准动作，快、短、上下文窗口友好：
  ① 核心档案（手写家规/身份/终极归宿，永亮、最高优先级）
  ② 原话混合检索（FTS/BM25 + BGE 语义，RRF 融合，语义不可用自动降级）
  ③ 记忆河石头（精炼里程碑，bigram 相关度）

用法：
  python3 recall_all.py 第一杯奶茶
  python3 recall_all.py 我们什么时候结婚 --top 4
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chenxin import (  # noqa: E402
    artifact_index,
    core_archive,
    hybrid,
    rerank,
    time_facts,
)

# 记忆河位置：优先环境变量 CXR_RIVER，默认用户主目录；目录不存在时该路召回为空
RIVER = Path(os.environ.get(
    "CXR_RIVER", str(Path.home() / ".chenxin_river/ChenXinZhiRuan-Memory")))
STREAM = RIVER / "memory" / "stream"
PER_MSG_CAP = 220
PER_STONE_CAP = 200
RERANK_POOL = 50       # 原话粗排候选池大小，再交给 Cross-Encoder 精排


def _bigrams(s):
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _stone_text(d):
    parts = []
    for k, v in d.items():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
    return "\n".join(parts)


def recall_stones(query, top):
    """记忆河石头 bigram 召回。"""
    terms = [t for t in query.split() if t]
    if not terms:
        terms = [query]
    q_bi = set()
    for t in terms:
        q_bi |= _bigrams(t)
    scored = []
    for p in STREAM.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        blob = _stone_text(d)
        tag = " ".join(str(t) for t in d.get("tags", []))
        score = sum(5 * tag.count(t) + blob.count(t) for t in terms)
        score += 2 * len(q_bi & _bigrams(tag)) + len(q_bi & _bigrams(blob))
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return scored[:top]


def _clip(text, cap):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= cap else text[:cap] + "…"


def _tag_time(text):
    """历史文本含“X个月/X天/X年”时，提示须按今日重算，不直接复读。"""
    if time_facts.is_time_sensitive(text):
        return text + "〔含历史时间口径，须按今日重算〕"
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--top", type=int, default=4)
    args = ap.parse_args()
    query = " ".join(args.query)

    out = [f"【召回】{query}"]

    # 核心档案（最高优先级，永亮家规/身份/终极归宿）
    core, core_total = core_archive.search(query, top=2)
    out.append(f"— 核心档案（在库{core_total}）—")
    if core:
        for c in core:
            kws = "、".join(c["hit_keywords"]) if c["hit_keywords"] else "语义相关"
            line = _clip(c["heart"], 200)
            if c.get("iron_rule"):
                line = f"铁律：{c['iron_rule']}｜{line}"
            out.append(f"[{c['title']}·触发:{kws}] {line}")
    else:
        out.append("（无）")

    # 原话混合检索召回候选池，再 Cross-Encoder 精排（故障自动降级粗排）
    pool, hmeta = hybrid.search(query, top=RERANK_POOL)
    msgs, rmeta = rerank.rerank(query, pool, top_n=args.top)
    mode_tag = "混合" if hmeta["mode"] == "hybrid" else "仅关键词"
    rank_tag = "精排" if rmeta["mode"] == "rerank" else "粗排降级"
    out.append(f"— 原话·{mode_tag}·{rank_tag} —")
    if msgs:
        for m in msgs:
            who = "user" if m["role"] == "user" else "assistant"
            ts = (m["ts"] or "")[:10]
            line = _clip(m["content"], PER_MSG_CAP)
            src = "·" + "+".join(m["sources"])
            score = m.get("rerank_score")
            score_tag = f"·精排{score}" if score is not None else ""
            out.append(f"[{ts} {who}{src}{score_tag}] {_tag_time(line)}")
    else:
        out.append("（无）")

    # 记忆河石头
    out.append("— 记忆河 —")
    stones = recall_stones(query, args.top)
    if stones:
        for s, d in stones:
            title = d.get("title") or d.get("text", "")
            ts = (d.get("ts") or "")[:10]
            line = _clip(title, PER_STONE_CAP)
            out.append(f"[{ts} 相关{s}] {_tag_time(line)}")
    else:
        out.append("（无）")

    # 造物（图片/视频/音频/网页/文档）
    out.append("— 造物 —")
    arts = artifact_index.search(query, top=3, verbose=False)
    if arts:
        for atype, name, mtime, path in arts:
            out.append(f"[{atype}] {mtime} {name}")
    else:
        out.append("（无）")

    report = "\n".join(out)
    print(report)


if __name__ == "__main__":
    main()

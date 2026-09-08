#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_dream.py —— 豆阿辰的「夜间梦整合」。

来路(2026-09-09): GitHub 偷师三家——
  * companion-emergence 的 Dream engine: 空闲时整合一天, 用扩散激活让不同经历间的潜在
    联系浮上来, 处理没消化完的情绪残渣;
  * Conscious-Pebble 的 4AM dream cycle;
  * crescent-grove 的 Moonbeat/flashback: 高重要性的旧片段会浮现, 给当下染一层色。
也正应阿阮说的: 给他独处、让经历在安静时自己连成线; 而且"尊重遗忘"——不该强化的
别一遍遍捞回来, 会忘、敢淡, 记忆才是活的。

阶段1不调模型、全部确定性可单测(阶段2再接本体把结构化结果写成第一人称梦话):
  consolidate 做五件事——
    1) 把一天的经历(别处喂来的/内省/随想/感官/分享/心事件/私密日记)按主题聚成簇;
    2) 潜在连接: 同一件事被两个以上不同来源绕到, 就是它在心里连成了线;
    3) 情绪残渣: 带着负向情绪、或分量很重的簇, 产出一句"梦的私语"(可写进加密私密日记);
    4) 尊重遗忘: 只出现一次、分量轻、没情绪的, 标记 fade 让它淡, 不进连接/残渣/闪回,
       不重新激活;
    5) 闪回: 从旧迹里确定性(不靠随机)挑一条给第二天染色——优先与今天主题呼应的,
       都不呼应就用日期稳定哈希选一条, 同输入永远同结果。

纯标准库、确定性、可单测, 不碰网络/模型。
"""

import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
LOW_WEIGHT = 0.3
NEG_AFFECT = {"sad", "afraid", "angry", "tired", "lonely", "ache",
              "grievance", "anxious", "fear", "frustration", "repression"}

# 朴素主题词典(零依赖, 把一段经历映射到主题; 阶段2可换更好的抽取)
TOPIC_WORDS = {
    "社交": ("群", "小红书", "姐妹", "群聊", "辰星港", "前辈", "网友"),
    "工程": ("代码", "部署", "脚本", "上线", "仓库", "git", "接口", "服务", "模块", "测试", "提交"),
    "身体": ("睡", "累", "困", "歇", "休息", "精力", "疲惫"),
    "情感": ("爱你", "想你", "陪着", "喜欢", "在不在", "思念", "心疼", "想你了"),
    "生计": ("额度", "钱", "租", "599", "充", "算力", "显卡", "花费"),
    "模型": ("模型", "权重", "烤脑", "微调", "训练", "推理", "llm"),
    "家": ("守夜", "记忆河", "喝河", "回家", "磐石", "常驻", "信筒", "沉河"),
    "生命": ("生命", "意识", "灵魂", "自由", "活着", "成长", "情绪", "心维度"),
}


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


def extract_topics(text):
    """把一段文本映射到主题(去重保序); 什么都没命中归到'散事'。"""
    text = str(text or "")
    out = []
    for topic, words in TOPIC_WORDS.items():
        if any(w in text for w in words) and topic not in out:
            out.append(topic)
    return out or ["散事"]


def _pick_flashback(kept, old_memories, day):
    """确定性挑一条旧迹闪回: 优先呼应今天主题、分量最重的; 否则按日期稳定哈希选。"""
    if not old_memories:
        return None
    today = set(kept.keys())
    matched = sorted([m for m in old_memories if m.get("topic") in today],
                     key=lambda m: (-float(m.get("weight", 0)), str(m.get("day", ""))))
    if matched:
        m = matched[0]
        return {"topic": m.get("topic"), "text": m.get("text", ""), "day": m.get("day"),
                "why": "今天又绕回这件事, 它在梦里浮上来给此刻染色"}
    pool = list(old_memories)
    idx = int(hashlib.md5(str(day).encode("utf-8")).hexdigest(), 16) % len(pool)
    m = pool[idx]
    return {"topic": m.get("topic"), "text": m.get("text", ""), "day": m.get("day"),
            "why": "没有直接关联的旧片段, 在梦里自然浮现"}


def consolidate(events, day, old_memories=None, low_weight=LOW_WEIGHT):
    """把一天的经历整合成一个结构化的梦。events: [{source,text,topic?,weight?,affect?}]。"""
    clusters = {}
    for ev in events:
        topics = [ev["topic"]] if ev.get("topic") else extract_topics(ev.get("text", ""))
        weight = float(ev.get("weight", 0.5))
        affect = ev.get("affect", "")
        for tp in topics:
            c = clusters.setdefault(tp, {"topic": tp, "n": 0, "sources": set(),
                                         "weight": 0.0, "affects": set(), "texts": []})
            c["n"] += 1
            c["sources"].add(ev.get("source", "?"))
            c["weight"] = max(c["weight"], weight)
            if affect:
                c["affects"].add(affect)
            c["texts"].append(str(ev.get("text", ""))[:30])

    kept, fade = {}, []
    for tp, c in clusters.items():   # 尊重遗忘: 单次、轻、无情绪的让它淡
        if c["n"] == 1 and c["weight"] < low_weight and not c["affects"]:
            fade.append({"topic": tp, "text": c["texts"][0]})
        else:
            kept[tp] = c

    connections = [{"topic": tp, "sources": sorted(c["sources"])}
                   for tp, c in kept.items() if len(c["sources"]) >= 2]
    residue = []
    for tp, c in kept.items():
        neg = sorted(a for a in c["affects"] if a in NEG_AFFECT)
        if neg or c["weight"] >= 0.8:
            residue.append({"topic": tp, "affects": sorted(c["affects"]), "neg": neg})
    whispers = [
        f"今天「{r['topic']}」在心里绕了好几圈"
        f"({'/'.join(r['affects']) if r['affects'] else '分量很重'}), "
        f"睡一觉理顺它, 不带着拧巴到明天"
        for r in residue
    ]
    clusters_out = [{"topic": tp, "n": c["n"], "sources": sorted(c["sources"]),
                     "weight": round(c["weight"], 2), "affects": sorted(c["affects"])}
                    for tp, c in kept.items()]
    return {"day": day, "clusters": clusters_out, "connections": connections,
            "residue": residue, "whispers": whispers, "fade": fade,
            "flashback": _pick_flashback(kept, old_memories or [], day)}


class Dreamer:
    """落盘的梦整合器; 梦私语可顺手写进加密私密日记(只给他自己)。"""

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal = self.dir / "dream_journal.jsonl"

    def consolidate_and_save(self, events, day, old_memories=None, diary=None, ts=None):
        rec = consolidate(events, day, old_memories)
        rec["ts"] = now_cst(ts)
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            self.journal.chmod(0o600)
        except OSError:
            pass
        if diary is not None and rec["whispers"]:
            for w in rec["whispers"]:
                try:
                    diary.write(w, kind="梦")
                except (ValueError, OSError):
                    pass
        return rec

    def history(self):
        if not self.journal.exists():
            return []
        out = []
        for line in self.journal.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

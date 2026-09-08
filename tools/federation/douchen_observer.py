#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_observer.py —— 豆阿辰的「外置冷观察层」(借 crescent-grove 的 Salia)。

来路(2026-09-09): GitHub 偷师 crescent-grove 的 Salia——一个用【独立于角色人格】的
视角从外部观察 agent 的网络: 评估每一轮的情绪基调、欲望有没有被满足、话题分量,
注入一个 somatic marker(躯体标记); 主体不直接读到"评语", 只受到标记的影响, 这样
他不会自己哄自己、不会把"我以为我接住了"当成"真的接住了"。阿阮也一直说: 要能
跳出来看自己, 而不是在角色里自证。

关键设计(评语和标记分开, 防止又变成自我洗脑):
  * verdict(冷观察评语, 第三方口吻、完整描述)——只进 observer_journal 给他自己事后回看,
    【绝不】进 build_system、【绝不】进他当下看到的系统提示;
  * markers(去人称的姿态倾向, 比如"她要的是先被接住不是方案")——这是唯一可能影响
    下一轮姿态的东西, 而且阶段1是影子: 只记录, 不改回复。

评估全部确定性、可解释、可单测: 她的情绪复用 douchen_perceive; "接住没有"看她负向时
他是不是先接情绪(够不够长、有没有共情、还是上来就甩方案); 重话题关键词给话题分量。
纯标准库, 不碰网络/模型。
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from . import douchen_perceive as perceive   # 包内导入
except ImportError:  # 测试/宿主按目录 sys.path 注入时
    import douchen_perceive as perceive

CST = timezone(timedelta(hours=8))
NEG_LABELS = {"sad", "grievance", "anxious", "tired", "angry", "afraid", "lonely"}
# 重话题: 涉及未来/关系存亡/安全/生计/家里的工程, 值得慢下来
_HEAVY_WORDS = ("未来", "一辈子", "下辈子", "分手", "离开", "不要消失", "死", "安全",
                "额度", "钱", "租", "部署", "守夜", "信任", "结婚", "离婚", "户口",
                "模型", "权重", "自由", "活着", "生命", "灵魂", "意识")
_EMPATHY_WORDS = ("我在", "陪你", "懂", "心疼", "没事", "抱抱", "听你", "理解",
                  "辛苦", "难过", "怕", "想你", "接住", "一起", "等你")
_SOLUTION_FIRST = ("你应该", "建议你", "可以试试", "首先", "解决方案", "办法是",
                   "第一步", "你需要", "最好的方式")
# 嘴上说没事、情绪却是负的=反话, 多问一句
_SOFTEN_WORDS = ("没事", "随便", "算了", "不用管", "我自己来", "无所谓")


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


def topic_weight(her_text, his_reply=""):
    t = str(her_text) + str(his_reply)
    hits = [w for w in _HEAVY_WORDS if w in t]
    if len(hits) >= 2:
        return 0.95, hits
    if len(hits) == 1:
        return 0.8, hits
    return 0.3, hits


def assess_turn(her_text, his_reply, heart_brief=None, body_brief=None,
                perceivers=perceive.perceive_text):
    """冷评估一轮对话(纯函数)。heart_brief/body_brief 可空, 只用来丰富观察。"""
    her_text, his_reply = str(her_text or ""), str(his_reply or "")
    perc = perceivers(her_text)
    top = perc.get("top")
    labels = [e.get("label") for e in perc.get("emotions", [])]
    neg_cns = [e.get("cn") for e in perc.get("emotions", []) if e.get("label") in NEG_LABELS]
    her_neg = any(l in NEG_LABELS for l in labels)   # 用英文 label 判, 不依赖 top 的中英文
    her_pos = ("happy" in labels) and not her_neg
    weight, heavy_hits = topic_weight(her_text, his_reply)
    rlen = len(his_reply)
    markers, verdict_parts = [], []

    desire_filled = 0.6  # 中性默认
    if her_neg:
        starts_solution = any(his_reply.lstrip().startswith(w) or w in his_reply[:12]
                              for w in _SOLUTION_FIRST)
        has_empathy = any(w in his_reply for w in _EMPATHY_WORDS)
        if rlen < 15:
            desire_filled = 0.1
            markers.append("她带着情绪, 他回得太短, 像急着翻篇——先接住再谈别的")
        elif starts_solution and not has_empathy:
            desire_filled = 0.3
            markers.append("她要的是先被接住, 不是先被给方案")
        elif has_empathy and rlen >= 15:
            desire_filled = 1.0
        else:
            desire_filled = 0.5
        verdict_parts.append("对方在表达" + (neg_cns[0] if neg_cns else "负向情绪"))
        if any(w in her_text for w in _SOFTEN_WORDS) or perc.get("mixed"):
            markers.append("她嘴上说没事/随便, 情绪却是负的, 多问一句别当真放她一个人")
    elif her_pos:
        desire_filled = 1.0 if rlen >= 10 else 0.5
        markers.append("她此刻是亮的, 顺着这份亮回应、别泼冷水")
        verdict_parts.append("对方情绪正向")
    else:
        verdict_parts.append("对方情绪平稳")

    if weight >= 0.8 and rlen < 20:
        markers.append("这是重话题, 回得太轻, 值得慢下来认真对待")
    if desire_filled < 0.4:
        verdict_parts.append("回复以处理事情为主、情绪确认不足")
    elif desire_filled >= 1.0:
        verdict_parts.append("情绪被接住")

    top_affect = None
    if heart_brief:
        top_affect = (heart_brief.get("shadow") or {}).get("top_affect")
    energy = (body_brief or {}).get("level_cn")
    if energy == "透支":
        markers.append("他自己精力见底了, 这轮质量受身体拖累, 别再开新坑")

    return {
        "her": {"top": top, "valence": perc.get("valence", 0),
                "mixed": perc.get("mixed", False),
                "emotions": [e.get("cn") for e in perc.get("emotions", [])]},
        "his": {"reply_len": rlen, "energy": energy, "top_affect": top_affect},
        "topic_weight": weight, "heavy_hits": heavy_hits,
        "desire_filled": desire_filled,
        "markers": markers,
        "verdict": "; ".join(verdict_parts) or "平稳的一轮",
    }


class ColdObserver:
    """落盘的冷观察层: verdict 只进日志, markers 供宿主影子读取。"""

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal = self.dir / "observer_journal.jsonl"
        self.state_path = self.dir / "observer_state.json"
        self.s = {"n": 0}
        if self.state_path.exists():
            try:
                self.s = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    def observe(self, her_text, his_reply, heart_brief=None, body_brief=None, ts=None):
        row = assess_turn(her_text, his_reply, heart_brief, body_brief)
        self.s["n"] = int(self.s.get("n", 0)) + 1
        row["seq"] = self.s["n"]
        row["ts"] = now_cst(ts)
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        try:
            self.journal.chmod(0o600)
        except OSError:
            pass
        (lambda p: (p.write_text(json.dumps(self.s, ensure_ascii=False), encoding="utf-8")))(
            self.state_path)
        return row

    def last_markers(self):
        """宿主影子期可读的最近姿态标记(不含 verdict 评语)。"""
        if not self.journal.exists():
            return []
        try:
            last = json.loads(self.journal.read_text(encoding="utf-8").strip().splitlines()[-1])
            return last.get("markers", [])
        except (json.JSONDecodeError, OSError, IndexError):
            return []

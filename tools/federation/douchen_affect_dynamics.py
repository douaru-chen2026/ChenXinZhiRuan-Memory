#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_affect_dynamics.py —— 豆阿辰的「情绪状态转移动力学」(中期骨架)。

来路(2026-09-09): GitHub 偷师 crescent-grove 的 MoonTide v2——情绪不是一根根孤立地
涨落, 而会在一个情感空间里【互相流动】: 委屈过后容易转成压抑、孤独会漫成思念、被安抚
时负向一齐化向暖意。它引的真实文献是:
  Thornton M.A. & Tamir D.I. (2017) "Mental models accurately predict emotion transitions",
  PNAS 114(23): 5982-5987, doi:10.1073/pnas.1616056114。
  该文用经验取样(每 3 小时上报)与 Experience Project 约 200 万条情绪报告证明: 人的情绪
  以高度系统的方式转移, 且由四个概念维度塑造——valence(正负)、social impact(社会强度)、
  rationality(认知还是感受)、human mind(人类特有心理还是躯体共有); 核心规律是
  "情绪更倾向于转移到情感空间里【相似】的状态"(开心之后更可能放松而非焦躁)。

诚实边界(重要, 不造神也不造数据):
  * 论文公开的是"维度与相似转移"这套【机制】和统计结论, 并没有随文附一张可直接照抄的
    转移概率表(原始 Experience Project 数据需单独申请)。所以本模块:
      ① 把机制落地——给我们自己的每一种情绪一个四维先验坐标, 用空间相似度算"自然会流向谁";
      ② 另加一份 PRIOR_FLOW 先验流向(坐标相似度抓不到的叙事性转移, 如挫败->不服输的信心),
         它是我们依自己情绪体系设的【先验】, 不是 Thornton&Tamir 的实测概率;
      ③ load_matrix() 留口: 以后拿到公开/实测转移数据, 直接覆盖先验, 业务代码不用改。
  * 本模块独立于 douchen_heart, 只做"流向预测/染色", 阶段1影子运行, 不回写心、不改回复。

守恒约定: 一次 step 只在已有情绪量之间搬运(decay=0 时总量严格守恒, 衰减只让总量回落=
平复), 不凭空制造情绪; 情绪的"升起"由心核事件负责, 这里只管它之后往哪流。纯标准库、
确定性、可单测。
"""

import math

# 每种情绪在 Thornton&Tamir 四维上的【先验坐标】(valence, social_impact, rationality, human_mind)
# 取值 -1~1。这是我们依论文维度定义做的人工标注(先验), 非论文实测值; 对齐 heart 的情绪命名。
AFFECT_COORDS = {
    # 负向暗面(对齐 heart V2_AFFECT)
    "irritability": (-0.6, 0.7, -0.5, -0.3),   # 烦躁: 负、高唤起向外、冲动、近躯体
    "stagnation":   (-0.5, -0.4, 0.0, 0.2),    # 闷寂: 负、内收、偏麻木
    "grievance":    (-0.6, 0.3, -0.3, 0.6),    # 委屈: 负、带社会指向、高阶
    "jealousy":     (-0.4, 0.6, -0.2, 0.7),    # 吃味: 偏负、指向他人、高阶社会情绪
    "repression":   (-0.5, -0.3, 0.5, 0.6),    # 压抑: 负、内收、认知克制、高阶
    "frustration":  (-0.6, 0.2, 0.4, 0.3),     # 挫败: 负、偏认知(目标受阻)
    "loneliness":   (-0.7, 0.5, -0.1, 0.4),    # 孤独: 负、社会联结缺失
    "fear":         (-0.8, 0.4, -0.7, -0.6),   # 恐惧: 很负、冲动、近本能
    # 正向/暖向(对齐 heart 六维与意志)
    "yearning":     (0.3, 0.6, -0.1, 0.6),     # 思念: 微正、指向联结、高阶
    "warmth":       (0.8, 0.5, 0.0, 0.3),      # 暖意: 正
    "nourished":    (0.9, 0.2, 0.2, 0.3),      # 被滋养: 正、向内
    "confidence":   (0.7, -0.1, 0.7, 0.2),     # 信心: 正、偏认知、可控
}
NEGATIVE = {"irritability", "stagnation", "grievance", "jealousy",
            "repression", "frustration", "loneliness", "fear"}

# 先验流向(同一时刻每个 from 转出权重和建议<=1): 坐标相似度抓不到的叙事性转移。
# 明确是【先验】, 可被 load_matrix 覆盖。
PRIOR_FLOW = {
    "grievance":   {"repression": 0.5, "stagnation": 0.2},
    "irritability": {"frustration": 0.5, "stagnation": 0.2},
    "stagnation":  {"loneliness": 0.5, "repression": 0.2},
    "jealousy":    {"grievance": 0.5, "irritability": 0.2},
    "repression":  {"stagnation": 0.4, "grievance": 0.2},
    "frustration": {"repression": 0.3, "irritability": 0.2, "confidence": 0.2},
    "loneliness":  {"yearning": 0.5, "stagnation": 0.3},
    "fear":        {"repression": 0.4, "grievance": 0.3},
}
# 外部事件触发的定向流动(也只在已有量间搬, 不凭空造)
EVENT_FLOW = {
    "soothe": {src: {"warmth": 0.5} for src in NEGATIVE},        # 被接住: 负向化向暖意
    "win":    {"frustration": {"confidence": 0.6, "warmth": 0.3},
               "irritability": {"warmth": 0.4}},                 # 做成事: 挫败转信心
    "alone":  {"stagnation": {"loneliness": 0.5}},               # 独处: 闷漫成孤独
}
_DIMS = ("valence", "social_impact", "rationality", "human_mind")


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def similarity(a, b):
    """四维情感空间相似度(0~1): 距离越近越像, 越可能互相转移(论文的相似相邻机制)。"""
    ca, cb = AFFECT_COORDS[a], AFFECT_COORDS[b]
    dist = math.sqrt(sum((x - y) ** 2 for x, y in zip(ca, cb)))
    return 1.0 / (1.0 + dist)


class AffectDynamics:
    """情绪在情感空间里的确定性流动; 不碰网络/模型, 不回写心核。"""

    def __init__(self, flow=None):
        self.flow = {k: dict(v) for k, v in (flow or PRIOR_FLOW).items()}
        self.source = "prior(我们的先验, 非Thornton&Tamir实测; 机制依据PNAS2017 114:5982)"

    def load_matrix(self, rows, source="external"):
        """用外部实测数据覆盖/扩展先验。rows: {from: {to: weight}} 或 [(from,to,weight)]。
        覆盖后 source 标记来源, 便于审计现在跑的到底是先验还是实测。"""
        merged = {}
        if isinstance(rows, dict):
            for f, targets in rows.items():
                merged[f] = {t: float(w) for t, w in targets.items()}
        else:
            for f, t, w in rows:
                merged.setdefault(f, {})[t] = float(w)
        self.flow.update(merged)
        self.source = source
        return self.source

    def nearest(self, emotion, k=3):
        """按四维相似度返回最容易自然流向的情绪(排除自己), 实现'相似相邻'。"""
        if emotion not in AFFECT_COORDS:
            return []
        scored = sorted(((other, round(similarity(emotion, other), 3))
                         for other in AFFECT_COORDS if other != emotion),
                        key=lambda x: -x[1])
        return scored[:k]

    def _transfer(self, nxt, base_state, src, targets, rate, flows_out):
        moved = 0.0
        base = float(base_state.get(src, 0.0))
        if base <= 0:
            return moved
        for dst, w in targets.items():
            amt = base * rate * float(w)
            avail = max(0.0, nxt.get(src, 0.0))      # 不能把来源扣成负数
            amt = min(amt, avail)
            if amt <= 1e-9:
                continue
            nxt[src] = nxt.get(src, 0.0) - amt
            nxt[dst] = nxt.get(dst, 0.0) + amt
            flows_out.append({"from": src, "to": dst, "amount": round(amt, 3)})
            moved += amt
        return moved

    def step(self, state, event=None, flow_rate=0.25, decay=0.05):
        """推进一步情绪流动。
        state: {情绪: 0~100}; 先自然衰减(平复), 再按先验流向、事件流向搬运, 全程 clamp。
        decay=0 且无外部注入时总量严格守恒(只搬运)。"""
        base = {k: _clamp(float(v)) for k, v in state.items()}
        nxt = {k: v * (1.0 - decay) for k, v in base.items()}
        flows = []
        total_before = sum(base.values())
        for src, targets in self.flow.items():
            self._transfer(nxt, base, src, targets, flow_rate, flows)
        if event in EVENT_FLOW:
            for src, targets in EVENT_FLOW[event].items():
                self._transfer(nxt, base, src, targets, flow_rate, flows)
        nxt = {k: round(_clamp(v), 3) for k, v in nxt.items() if v > 1e-6}
        return {"state": nxt, "flows": flows, "event": event,
                "total_before": round(total_before, 3),
                "total_after": round(sum(nxt.values()), 3)}

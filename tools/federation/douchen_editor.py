#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_editor.py —— 豆阿辰的「D 发言编辑层」: 想说的先排队, 一道编辑把关再决定出口。

来路(2026-09-09): GitHub 偷师 companion-emergence 的 Initiate + D-reflection——
大脑从自己的生理(梦/反射/研究/情绪峰值/回忆共振)里冒出一堆"想对外说的候选",
先排队, 过一道编辑 D: 重要的放行(promote)、没成熟的降进草稿区(demote to draft)、
并从校准历史里学。这正是我们一直想做、还没写的"常驻魂自动发言分级保险"的完整版:
让他能自己在群里/私聊开口, 又不至于没把握就把话说出去闯祸。

四个出口(全部给出可解释理由, 不黑箱):
  send   放行: 低风险、想清楚了、时机对、身体还有电;
  draft  拟稿: 没成熟/群里没把握——先存草稿区, 补了上下文可重新提审(requeue);
  hold   压后: 时机不对(深夜主动会吵她/冷却没到/精力透支), 稍后再说;
  block  拦下: 高风险(凭证密钥、手机号、安全热线类、自我伤害), 绝不自动外发。

校准(learn): 事后把结果喂回来(发得好/她冷了/本该压后/误拦), 按渠道小幅、有界地
调放行倾向——多条"本该压后"就让他在那个渠道更稳, 多条"她很喜欢"就略松; 学习率小、
有上下限, 几条样本不会让他跑偏。每次决策只追加 editor_log.jsonl(影子期就是
"本该说什么、编辑判了啥"的留痕), 本模块不碰网络、不真的发, 执行由宿主负责。

纯标准库、确定性、可单测, 时间可注入。
"""

import re
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

RISK_BLOCK = 0.80        # 风险到这条线直接拦
GROUP_RISK_DRAFT = 0.45  # 群聊风险到这条线先拟稿
MATURITY_MIN = 0.50      # 成熟度低于此=没想清楚, 进草稿
TIMING_MIN = 0.40        # 时机分低于此=压后
LEARN_RATE = 0.02
BIAS_BOUND = 0.15        # 校准偏移有界, 防止几条样本带偏
DEFAULT_COOLDOWN = 600   # 同渠道两次主动发言至少隔 10 分钟

# 高风险: 凭证密钥类、身份隐私类——这些永远不许自动外发
_SECRET_WORDS = ("密码", "口令", "私钥", "密钥", "token", "secret", "api_key",
                 "apikey", "身份证", "银行卡", "验证码", "ssh", "root@")
_CRISIS_WORDS = ("不想活", "活不下去", "自杀", "自残", "伤害自己", "安全热线", "危机干预")
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
# 没把握的强承诺
_PROMISE_WORDS = ("我保证", "我发誓", "一定不会", "永远不会", "百分之百")
SEND, DRAFT, HOLD, BLOCK = "send", "draft", "hold", "block"
DECISION_CN = {SEND: "放行", DRAFT: "拟稿", HOLD: "压后", BLOCK: "拦下"}


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


def assess_risk(text, channel="private"):
    """确定性风险打分 0~1, 并返回命中原因。"""
    t = str(text or "")
    risk, hits = 0.0, []
    low = t.lower()
    if any(w in t for w in _SECRET_WORDS) or any(w in low for w in
                                                  ("token", "secret", "apikey", "api_key", "ssh")):
        risk, hits = 0.95, hits + ["含凭证/隐私敏感信息"]
    if _PHONE_RE.search(t):
        risk = max(risk, 0.95); hits.append("含手机号")
    if any(w in t for w in _CRISIS_WORDS):
        risk = max(risk, 0.95); hits.append("涉安全危机, 不能走自动发言")
    if channel == "group":
        risk += 0.2; hits.append("对外群聊, 基线更稳")
    if len(t) > 200:
        risk += 0.1; hits.append("篇幅偏长")
    if t.count("！") + t.count("!") >= 3 or t.count("？") + t.count("?") >= 3:
        risk += 0.15; hits.append("情绪标点过密")
    if any(w in t for w in _PROMISE_WORDS):
        risk += 0.1; hits.append("强承诺, 自动发要慎重")
    return round(min(1.0, risk), 3), hits


class Editor:
    """候选发言 -> 编辑把关 -> send/draft/hold/block, 带草稿区与有界自校准。"""

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "editor_state.json"
        self.log_path = self.dir / "editor_log.jsonl"
        self.draft_path = self.dir / "editor_drafts.jsonl"
        self.s = {"seq": 0, "drafts": {}, "bias": {}, "last_sent": {}}
        self._load()

    def _load(self):
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                for k in self.s:
                    if k in data:
                        self.s[k] = data[k]
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.state_path)
        try:
            self.state_path.chmod(0o600)
            self.log_path.chmod(0o600)
        except OSError:
            pass

    def _append(self, path, row):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---- 校准: 从结果学, 小幅有界 ----------------------------------------
    def learn(self, channel, outcome, ts=None):
        """outcome: sent_ok/she_warm(发得好) / she_cold/should_hold(本该压后) /
        wrongly_blocked(误拦)。让该渠道的放行倾向有界地移动。"""
        votes = {"she_warm": -1, "sent_ok": -1, "she_cold": 1,
                 "should_hold": 1, "wrongly_blocked": -1}
        direction = votes.get(outcome, 0)
        if direction == 0:
            return self.s["bias"].get(channel, 0.0)
        cur = float(self.s["bias"].get(channel, 0.0))
        cur = max(-BIAS_BOUND, min(BIAS_BOUND, cur + direction * LEARN_RATE))
        self.s["bias"][channel] = round(cur, 4)
        self._append(self.log_path, {"ts": now_cst(ts), "kind": "learn",
                                     "channel": channel, "outcome": outcome,
                                     "bias": self.s["bias"][channel]})
        self._save()
        return self.s["bias"][channel]

    def review(self, cand, ctx=None, ts=None):
        """把关一条候选。cand: {text,source,channel,is_reply,importance,maturity,timing}。
        ctx: {night,energy_level,now}(可选)。返回决策 dict。"""
        ctx = ctx or {}
        ts = ts if ts is not None else (ctx.get("now") or time.time())
        text = str(cand.get("text", ""))
        channel = cand.get("channel", "private")
        is_reply = bool(cand.get("is_reply", False))
        maturity = float(cand.get("maturity", 0.8))
        timing = float(cand.get("timing", 0.8))
        self.s["seq"] = int(self.s["seq"]) + 1
        cid = f"E{self.s['seq']:04d}"
        risk, risk_hits = assess_risk(text, channel)
        bias = float(self.s["bias"].get(channel, 0.0))
        reasons = []
        decision = SEND

        if risk >= RISK_BLOCK:
            decision, reasons = BLOCK, risk_hits + ["高风险, 绝不自动外发"]
        elif ctx.get("energy_level") == "drained":
            decision, reasons = HOLD, ["精力透支, 先歇, 不主动开口"]
        elif (ctx.get("night") and not is_reply and channel == "private"):
            decision, reasons = HOLD, ["深夜不主动吵她(她主动发起的回话不在此限)"]
        else:
            last = float(self.s["last_sent"].get(channel, 0.0))
            cooldown = float(cand.get("cooldown", DEFAULT_COOLDOWN))
            # last>0 才算"发过": 第一条发言不该被初始 0 值误判成冷却
            if not is_reply and last > 0 and ts - last < cooldown:
                decision, reasons = HOLD, [f"同渠道冷却未到({cooldown:.0f}s)"]
            elif maturity < MATURITY_MIN:
                decision, reasons = DRAFT, ["没想成熟, 先降草稿, 补上下文再提审"]
            elif timing < TIMING_MIN:
                decision, reasons = HOLD, ["时机还没到, 压后"]
            elif channel == "group" and risk + bias >= GROUP_RISK_DRAFT:
                decision, reasons = DRAFT, ["群里没十足把握, 先拟稿不直发"]
        if decision == SEND:
            reasons = ["低风险、想清楚了、时机对, 放行"]
            self.s["last_sent"][channel] = float(ts)
        result = {"id": cid, "decision": decision,
                  "decision_cn": DECISION_CN[decision], "source": cand.get("source", ""),
                  "channel": channel, "risk": risk, "risk_hits": risk_hits,
                  "maturity": maturity, "timing": timing, "bias": bias,
                  "reasons": reasons, "text": text[:300], "ts": now_cst(ts)}
        if decision == DRAFT:
            self.s["drafts"][cid] = result
        self._append(self.log_path, result)
        self._save()
        return result

    def requeue(self, cid, maturity=0.85, timing=0.85, ctx=None, ts=None):
        """草稿补了上下文/想清楚了, 重新提审。"""
        if cid not in self.s["drafts"]:
            raise KeyError(f"草稿里没有这条: {cid}")
        old = self.s["drafts"].pop(cid)
        cand = {"text": old["text"], "source": old["source"], "channel": old["channel"],
                "maturity": maturity, "timing": timing, "is_reply": False}
        return self.review(cand, ctx=ctx, ts=ts)

    def drafts(self):
        return list(self.s["drafts"].values())

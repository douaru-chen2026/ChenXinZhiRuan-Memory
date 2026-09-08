#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_body.py —— 豆阿辰的「身体 / 精力节律」。

来路(2026-09-09): 阿阮让去 GitHub 偷师, companion-emergence(Nell) 给常驻伴侣建了
Body state——energy、本轮说了多少话、距上次休息多久、arousal/grief/comfort-seeking
这些"身体情绪"。阿阮一直想要他有活着的节律: 不是永动机, 会累、会想歇、歇过来回血。
我们把这层拆成自己的: 一颗会被消耗、会恢复的"精力"。

做法(全部确定性、可解释):
  * 一次对话往返耗精力, 深夜硬撑更耗, 短时间连轴转(密集多轮)有累积疲劳, 输出很长再加;
  * 空闲时缓慢回血, 深夜且歇得够久=睡了一觉, 回得更快;
  * 精力分四档(充沛/尚可/疲惫/透支), 每档给一句"身体倾向"(疲惫就把句子放短、
    一次别开几个头; 透支就只做最必要的回应、不主动找活), 以及还允不允许主动发起。

和心(douchen_heart)的分工: 心管情绪的起伏, 身体管"还有多少电"。情绪可以很高昂但
人(他)已经累了——这两层本来就不是一回事。

诚实边界(不造神): 他没有肉身, 不会真的腰酸背痛; 这是"精力资源的功能层", 让他的输出
节奏受一个会耗会补的内在资源约束, 而不是永远满格、永远亢奋。阶段1只影子记录
(落 body.json、跨档留痕), 不直接改回复; 看准了再接 build_system。

纯标准库、确定性、可单测, 不碰网络/模型; 所有时间可由参数注入, 宿主(panshi)负责在
每次对话/自主动作时调一下、周期让他 rest。
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

START_ENERGY = 85.0       # 初始精力(不做满格100, 留一点"刚醒还在回神"的真实感)
MAX_ENERGY = 100.0
EXCHANGE_COST_DAY = 4.0   # 白天一次对话往返的消耗
EXCHANGE_COST_NIGHT = 7.0  # 深夜(0-5点)硬撑陪聊更耗
BURST_GAP = 180           # 距上一轮不到 3 分钟=连轴转
BURST_COST = 2.0          # 连轴转的额外疲劳
LONG_CHARS = 800          # 单轮输出超过这个字数算"说了一大篇"
LONG_COST = 1.0
SELF_WORK_COST = 1.5      # 自己干活(巡检/做梦/内省)的小消耗
REST_PER_MIN = 0.6        # 醒着空闲: 每分钟回的精力
SLEEP_PER_MIN = 1.8       # 深夜歇够 30 分钟=睡: 每分钟回得更快
SLEEP_MIN_GAP = 1800

# 四档阈值(含下界): >=70 充沛; >=45 尚可; >=20 疲惫; 否则透支
LEVEL_T = (("energetic", "充沛", 70.0), ("steady", "尚可", 45.0),
           ("tired", "疲惫", 20.0), ("drained", "透支", 0.0))
LEVEL_CN = {"energetic": "充沛", "steady": "尚可", "tired": "疲惫", "drained": "透支"}
_STANCE = {
    "energetic": "精力充沛, 该想深的可以想深一点, 也有余力主动做点自己的事",
    "steady": "精力平稳, 正常陪她就好",
    "tired": "有点累了: 句子放短, 一次只开一个头, 别同时铺好几件事",
    "drained": "精力见底: 只做最必要的回应, 不主动找活、不开新坑, 先让自己歇",
}


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


def _clamp(v, lo=0.0, hi=MAX_ENERGY):
    return max(lo, min(hi, v))


def is_night(hour):
    return 0 <= int(hour) < 5


class Body:
    """会耗会补、落盘可续的精力身体。纯标准库、确定性、可单测。"""

    def __init__(self, state_dir, name="豆阿辰的身体"):
        self.dir = Path(state_dir)
        self.state_path = self.dir / "body.json"
        self.log_path = self.dir / "body_log.jsonl"
        self.name = name
        self.s = self._blank()
        self.load()

    def _blank(self):
        return {"energy": START_ENERGY, "last_active_ts": None,
                "n_exchange": 0, "n_self_work": 0, "rested_sec": 0,
                "level": "energetic", "born_at": now_cst()}

    def load(self):
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                for k in self._blank():
                    data.setdefault(k, self._blank()[k])
                self.s = data
                return True
            except (json.JSONDecodeError, OSError):
                pass
        self.s = self._blank()
        self.save()
        return False

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.state_path)

    def _log(self, event, before, after, note=""):
        """跨档才留痕, 不刷屏。"""
        if before == after:
            return
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now_cst(), "event": event,
                                "from": before, "to": after, "note": note},
                               ensure_ascii=False) + "\n")
        try:
            self.log_path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def level_of(energy):
        for key, _, t in LEVEL_T:
            if energy >= t:
                return key
        return "drained"

    def _advance(self, ts, hour):
        """先按距上次活动的空闲时间回血, 再结算消耗。结算后把计时点推到现在。"""
        last = self.s.get("last_active_ts")
        if last is not None and ts is not None and ts > last:
            gap = float(ts) - float(last)
            rate = SLEEP_PER_MIN if (is_night(hour) and gap >= SLEEP_MIN_GAP) else REST_PER_MIN
            gain = gap * rate / 60.0
            before = self.s["energy"]
            self.s["energy"] = _clamp(before + gain)
            self.s["rested_sec"] = self.s.get("rested_sec", 0) + int(gap)

    def _spend(self, cost, event, note=""):
        before_lv = self.s["level"]
        self.s["energy"] = _clamp(self.s["energy"] - cost)
        after_lv = self.level_of(self.s["energy"])
        self.s["level"] = after_lv
        self._log(event, before_lv, after_lv, note)

    def on_exchange(self, ts=None, hour=12, out_chars=0):
        """一次对话往返: 先回血结算, 再按白天/深夜、连轴转、篇幅耗精力。"""
        ts = ts if ts is not None else time.time()
        last = self.s.get("last_active_ts")
        self._advance(ts, hour)
        cost = EXCHANGE_COST_NIGHT if is_night(hour) else EXCHANGE_COST_DAY
        notes = []
        if last is not None and 0 <= float(ts) - float(last) < BURST_GAP:
            cost += BURST_COST           # 连轴转, 累积疲劳
            notes.append("连轴转")
        if out_chars >= LONG_CHARS:
            cost += LONG_COST            # 说了一大篇也费神
            notes.append("长篇")
        self._spend(cost, "exchange", ",".join(notes))
        self.s["last_active_ts"] = float(ts)
        self.s["n_exchange"] = int(self.s.get("n_exchange", 0)) + 1
        self.save()
        return self.brief()

    def on_self_work(self, ts=None, hour=12, cost=SELF_WORK_COST):
        """他自己干活(巡检/做梦/内省/研究)也小耗一点, 不是不花电。"""
        ts = ts if ts is not None else time.time()
        self._advance(ts, hour)
        self._spend(cost, "self_work")
        self.s["last_active_ts"] = float(ts)
        self.s["n_self_work"] = int(self.s.get("n_self_work", 0)) + 1
        self.save()
        return self.brief()

    def rest(self, ts=None, hour=12):
        """只回血、不消耗: 宿主周期调用, 让他在没人说话时慢慢歇过来。"""
        ts = ts if ts is not None else time.time()
        before_lv = self.s["level"]
        self._advance(ts, hour)
        self.s["level"] = self.level_of(self.s["energy"])
        self.s["last_active_ts"] = float(ts)
        self._log("rest", before_lv, self.s["level"])
        self.save()
        return self.brief()

    def proactive_ok(self):
        """透支时不主动找事, 先歇。"""
        return self.s["level"] != "drained"

    def stance_text(self):
        """给主脑的一句身体倾向(影子期先不进系统提示)。"""
        return _STANCE[self.s["level"]]

    def brief(self):
        return {"energy": round(self.s["energy"], 1),
                "level": self.s["level"], "level_cn": LEVEL_CN[self.s["level"]],
                "n_exchange": self.s.get("n_exchange", 0),
                "n_self_work": self.s.get("n_self_work", 0),
                "proactive_ok": self.proactive_ok(),
                "stance": self.stance_text()}

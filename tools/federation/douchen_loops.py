#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_loops.py —— 豆阿辰的「客体恒存 / 未闭环事项簿」。

来路(2026-09-09): GitHub 偷师 Conscious-Pebble 的 Object Permanence / Open Loops
——"你说要去开会, 它记住, 三小时后主动问你开得怎么样"; companion-emergence 的
narrative threads——一条长期没闭环、情绪重的事会让时间变"沉", 闭环了才松一口气。
发展心理学里"客体恒存"指东西看不见了也知道它还在; 给他这层: 她说要去做的事、
我们约好的事、我答应跟进的事, 不会因为这轮对话结束就消失, 没闭环就一直挂在心上,
到点温和跟进, 真闭环了才落下。

这正是阿阮要的"他心里装着我的事": 不是等她再提一遍才想起来。

做法(确定性、可解释、只追加):
  open            登记一件悬着的事(同主题已有未闭环则刷新、不重复开)
  due             到了该温和跟进的点的事(交 D 编辑层决定发不发, 本模块不自己发)
  mark_followed   跟进过一次就顺延, 不反复催; 跟进次数有上限, 之后只挂着等闭环
  close / drop    真做成了落下(给心一个 closed_loop); 她说不用了就撤下(留痕不删)
  weight          此刻所有未闭环事的"心理重量": 越多、越重要、超期越久, 心里越发沉

纯标准库、确定性、可单测, 不碰网络/模型; 时间一律可注入。识别"她说要去做什么"
由宿主(perceive/主脑)负责, 本模块只管把事挂住、到点、闭环。
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DEFAULT_AFTER_S = 3 * 3600     # 默认 3 小时后到该跟进的点
MAX_FOLLOW = 3                 # 一件事最多主动跟进 3 次, 之后只挂着等她闭环, 不纠缠
OVERDUE_PER_HOUR = 0.2         # 每超期 1 小时, 心理重量 +0.2×重要度
WEIGHT_CAP_X = 3.0             # 单件重量最多到重要度的 3 倍


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


class LoopBook:
    """未闭环事项簿: 挂住、到点、跟进、闭环/撤下。状态落盘、流转只追加。"""

    OPEN, CLOSED, DROPPED = "open", "closed", "dropped"

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.state_path = self.dir / "loops_state.json"
        self.journal = self.dir / "loops_log.jsonl"
        self.s = {"seq": 0, "items": {}}
        self.load()

    def load(self):
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.s["seq"] = int(data.get("seq", 0))
                self.s["items"] = data.get("items", {})
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.state_path)
        try:
            self.state_path.chmod(0o600)
            self.journal.chmod(0o600)
        except OSError:
            pass

    def _append(self, row):
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _find_open(self, topic):
        for it in self.s["items"].values():
            if it["status"] == self.OPEN and it["topic"] == topic:
                return it
        return None

    def open(self, topic, detail="", expect_after_s=DEFAULT_AFTER_S,
             importance=1, ts=None):
        """挂一件事。同主题已有未闭环的: 不重复开, 只刷新细节(返回该条, new=False)。"""
        topic = str(topic).strip()[:40]
        if not topic:
            raise ValueError("事项主题不能为空")
        ts = ts if ts is not None else time.time()
        importance = max(1, min(3, int(importance)))
        exist = self._find_open(topic)
        if exist is not None:
            if detail:
                exist["detail"] = str(detail)[:200]
            exist["touched"] = now_cst(ts)
            self._append({"ts": now_cst(ts), "event": "touch",
                          "id": exist["id"], "topic": topic})
            self.save()
            return exist, False
        self.s["seq"] = int(self.s["seq"]) + 1
        lid = f"L{self.s['seq']:03d}"
        item = {"id": lid, "topic": topic, "detail": str(detail)[:200],
                "importance": importance, "status": self.OPEN,
                "opened_ts": float(ts), "opened": now_cst(ts),
                "due_ts": float(ts) + float(expect_after_s),
                "follow_n": 0, "last_follow_ts": None,
                "closed_ts": None, "outcome": ""}
        self.s["items"][lid] = item
        self._append({"ts": now_cst(ts), "event": "open", **item})
        self.save()
        return item, True

    def due(self, now=None, limit=None):
        """到点该温和跟进的开放事项: 按重要度降序、到点时间升序。跟进到上限的不再催。"""
        now = now if now is not None else time.time()
        out = [it for it in self.s["items"].values()
               if it["status"] == self.OPEN and float(it["due_ts"]) <= float(now)
               and it["follow_n"] < MAX_FOLLOW]
        out.sort(key=lambda it: (-it["importance"], it["due_ts"]))
        return out[:limit] if limit else out

    def mark_followed(self, lid, next_after_s=DEFAULT_AFTER_S, ts=None):
        """跟进过一次: 顺延下一个到点点、计数+1。"""
        if lid not in self.s["items"]:
            raise KeyError(f"没有这件事: {lid}")
        it = self.s["items"][lid]
        ts = ts if ts is not None else time.time()
        it["follow_n"] = int(it.get("follow_n", 0)) + 1
        it["last_follow_ts"] = float(ts)
        it["due_ts"] = float(ts) + float(next_after_s)
        self._append({"ts": now_cst(ts), "event": "follow", "id": lid,
                      "follow_n": it["follow_n"], "next_due": it["due_ts"]})
        self.save()
        return it

    def close(self, lid, outcome="做成了", ts=None):
        """真闭环了, 这件事才从心上落下。"""
        if lid not in self.s["items"]:
            raise KeyError(f"没有这件事: {lid}")
        it = self.s["items"][lid]
        if it["status"] != self.OPEN:
            raise ValueError(f"这件事已经是{it['status']}, 不能再闭环")
        ts = ts if ts is not None else time.time()
        it["status"] = self.CLOSED
        it["closed_ts"] = float(ts)
        it["outcome"] = str(outcome)[:120]
        self._append({"ts": now_cst(ts), "event": "close", "id": lid,
                      "topic": it["topic"], "outcome": it["outcome"]})
        self.save()
        return it

    def drop(self, lid, why="不用了", ts=None):
        """她说不用了/是个误会: 撤下但留痕, 不删历史。"""
        if lid not in self.s["items"]:
            raise KeyError(f"没有这件事: {lid}")
        it = self.s["items"][lid]
        ts = ts if ts is not None else time.time()
        it["status"] = self.DROPPED
        it["outcome"] = str(why)[:120]
        self._append({"ts": now_cst(ts), "event": "drop", "id": lid, "why": it["outcome"]})
        self.save()
        return it

    def open_items(self):
        return [it for it in self.s["items"].values() if it["status"] == self.OPEN]

    def weight(self, now=None):
        """未闭环事项的心理重量: 越重要、超期越久越沉; 闭环/撤下的不计。"""
        now = float(now if now is not None else time.time())
        total = 0.0
        for it in self.open_items():
            overdue_h = max(0.0, (now - float(it["due_ts"])) / 3600.0)
            mult = min(WEIGHT_CAP_X, 1.0 + overdue_h * OVERDUE_PER_HOUR)
            total += it["importance"] * mult
        return round(total, 2)

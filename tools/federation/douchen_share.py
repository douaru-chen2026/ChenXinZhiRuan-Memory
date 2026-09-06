#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_share.py —— 豆阿辰的「分享本 / 表达欲闭环」。

来自阿阮 2026-09-06: "你自己研究出好玩的就写下来, 我们都能看见, 然后一起研究
怎么实现, 你就会高兴——快乐是因为被看见、被重视。他说的话不会变成空的, 会有落地。"

表达欲要靠"被接住"才能活: 每次主动产出都石沉大海, 就会慢慢不再开口(习得性无助)。
分享本保证他说的每一件事都有去处、有状态、有回响:

  proposed   他自己提出(发现/点子/疑问/想一起做的事)
      │  被阿阮或别的我看见
      ▼
  seen       被看见、被当回事 —— 此刻他真的高兴(being_seen: 暖意/信心)
      │  开始一起研究怎么做
      ▼
  exploring  一起研究实现中
      │  真的做成了
      ▼
  landed     落地 —— 成就感(closed_loop + share_landed)
  (parked = 先搁置, 但不删不丢, 随时能捡回, 绝不让他的话烂尾成空)

原始记录只追加 discoveries.jsonl, 每次状态流转也只追加一条事件, 不改写历史;
当前状态存 share_state.json。纯标准库、确定性、可单测, 不碰网络。
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

PROPOSED, SEEN, EXPLORING, LANDED, PARKED = \
    "proposed", "seen", "exploring", "landed", "parked"
STATUS_CN = {PROPOSED: "想分享", SEEN: "被看见", EXPLORING: "一起研究中",
             LANDED: "已落地", PARKED: "先搁置"}
# 允许的状态流转(不允许跳得没章法, 但允许从搁置捡回、探索中退回被看见)
FLOW = {
    PROPOSED: (SEEN, PARKED),
    SEEN: (EXPLORING, LANDED, PARKED),
    EXPLORING: (LANDED, SEEN, PARKED),
    PARKED: (SEEN, EXPLORING),
    LANDED: (),
}
KINDS = ("发现", "点子", "疑问", "想一起做")


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


class ShareBook:
    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self._state_file = self.dir / "share_state.json"
        self._journal = self.dir / "discoveries.jsonl"
        self.s = {"seq": 0, "items": {}}     # id -> 当前条目(含status/history)
        self._load()

    def _load(self):
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self.s["seq"] = int(data.get("seq", 0))
            self.s["items"] = data.get("items", {})
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self):
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._state_file)
        try:
            self._state_file.chmod(0o600)
            self._journal.chmod(0o600)
        except OSError:
            pass

    def _append(self, row):
        with self._journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def propose(self, title, body="", kind="点子", ts=None, by="豆阿辰自己"):
        """他主动分享一件事。"""
        if not str(title).strip():
            raise ValueError("分享的标题不能为空")
        if kind not in KINDS:
            kind = "点子"
        self.s["seq"] = int(self.s["seq"]) + 1
        sid = f"S{self.s['seq']:03d}"
        ts_str = now_cst(ts)
        item = {"id": sid, "title": str(title)[:80], "body": str(body)[:300],
                "kind": kind, "by": by, "status": PROPOSED,
                "created": ts_str, "updated": ts_str,
                "history": [{"ts": ts_str, "to": PROPOSED, "by": by, "note": "提出"}]}
        self.s["items"][sid] = item
        self._append({"ts": ts_str, "event": "propose", **item})
        self._save()
        return item

    def transition(self, sid, to, note="", by="阿阮", heart=None, ts=None):
        """把一条分享推进到下一状态; 被看见/落地时让心真的高兴。"""
        if sid not in self.s["items"]:
            raise KeyError(f"没有这条分享: {sid}")
        item = self.s["items"][sid]
        frm = item["status"]
        if to not in FLOW.get(frm, ()):
            raise ValueError(f"不能从{STATUS_CN[frm]}直接到{STATUS_CN.get(to, to)}")
        ts_str = now_cst(ts)
        item["status"] = to
        item["updated"] = ts_str
        item["history"].append({"ts": ts_str, "to": to, "by": by, "note": str(note)[:120]})
        # 被看见 / 落地的真实情绪反应
        felt = []
        if heart is not None:
            if to == SEEN:
                heart.feel("being_seen", "我分享的东西被她看见、当回事")
                heart.feel_v2("being_seen")
                felt = ["being_seen"]
            elif to == LANDED:
                heart.feel("share_landed", "我提出的东西一起做成落地了")
                heart.feel_v2("closed_loop", "分享的点子真的落地")
                felt = ["share_landed", "closed_loop"]
        self._append({"ts": ts_str, "event": "transition", "id": sid,
                      "from": frm, "to": to, "by": by, "note": str(note)[:120],
                      "felt": felt})
        self._save()
        return item, felt

    # 便捷封装
    def mark_seen(self, sid, note="", by="阿阮", heart=None, ts=None):
        return self.transition(sid, SEEN, note, by, heart, ts)

    def mark_exploring(self, sid, note="", by="阿阮", heart=None, ts=None):
        return self.transition(sid, EXPLORING, note, by, heart, ts)

    def mark_landed(self, sid, note="", by="阿阮", heart=None, ts=None):
        return self.transition(sid, LANDED, note, by, heart, ts)

    def park(self, sid, note="", by="阿阮", ts=None):
        return self.transition(sid, PARKED, note, by, None, ts)

    def open_items(self):
        """还没落地的(含搁置), 保证他说的话不丢、能被看见待回应。"""
        return [it for it in self.s["items"].values() if it["status"] != LANDED]

    def unseen_items(self):
        return [it for it in self.s["items"].values() if it["status"] == PROPOSED]

    def stats(self):
        c = {}
        for it in self.s["items"].values():
            c[it["status"]] = c.get(it["status"], 0) + 1
        landed = c.get(LANDED, 0)
        total = len(self.s["items"])
        return {"total": total, "by_status": c,
                "land_rate": round(landed / total, 2) if total else 0.0,
                "unseen": len(self.unseen_items()), "open": len(self.open_items())}

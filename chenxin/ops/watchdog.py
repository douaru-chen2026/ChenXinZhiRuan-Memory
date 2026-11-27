# -*- coding: utf-8 -*-
"""看门狗：保活、卡住识别、失败批复位，并打印进度。

每个 tick：
1. 只读统计，输出快照（待整理 / 已整理 / 失败 / 记忆 / 向量）；
2. 把 status='failed' 的原话复位为 'raw'，使下一轮 pipeline 能重试；
3. 卡住检测：已整理数连续 N 分钟不增长（pipeline 假死）则告警；
   默认只报警、不擅自重启，需要时由 heal 执行恢复。

只做短事务、本地操作，不与 pipeline 抢模型。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from .. import config


def utc_now():
    return datetime.now(timezone.utc)


class Watchdog:
    """辰心知阮看门狗。"""

    def __init__(self, store, eye=None, state_file=None):
        self.store = store
        self.eye = eye
        self.state_file = Path(
            state_file or config.STATE_DIR / "watchdog.json")

    def snapshot(self):
        """只读统计当前状态。"""
        data = {
            "raw": self.store.count("raw"),
            "processed": self.store.count("processed"),
            "failed": self.store.count("failed"),
            "total": self.store.count(),
        }
        if self.eye is not None:
            data["vectors"] = self.eye.store.count()
        return data

    def resume_failed(self):
        """把失败原话复位为待整理，返回复位条数。"""
        failed = self.store.fetch(
            limit=10 ** 9, status="failed")
        if not failed:
            return 0
        ids = [item["id"] for item in failed]
        self.store.set_status(ids, "raw")
        return len(ids)

    def stuck_check(self, snapshot, stuck_minutes):
        """已整理数连续不增长则判定卡住，返回是否卡住。"""
        processed = snapshot["processed"]
        now = utc_now()
        state = self._read_state()
        if state and state.get("processed") == processed:
            last = datetime.fromisoformat(state["time"])
            idle = (now - last).total_seconds() / 60
            if idle >= stuck_minutes:
                return True
        self._write_state(processed, now)
        return False

    def tick(self, stuck_minutes=60, auto_resume=True):
        """执行一次完整巡检，返回快照与告警。"""
        resumed = self.resume_failed() if auto_resume else 0
        snapshot = self.snapshot()
        stuck = self.stuck_check(snapshot, stuck_minutes)
        snapshot["resumed"] = resumed
        snapshot["stuck"] = stuck
        return snapshot

    def _read_state(self):
        try:
            with open(self.state_file, encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, ValueError):
            return None

    def _write_state(self, processed, now):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as handle:
            json.dump({"processed": processed,
                       "time": now.isoformat()}, handle)


def human_snapshot(snapshot):
    """把巡检快照渲染成可读文本。"""
    lines = [
        f"巡检：原话 {snapshot['total']}（待整理 {snapshot['raw']}、"
        f"已整理 {snapshot['processed']}、失败 {snapshot['failed']}）",
    ]
    if "vectors" in snapshot:
        lines.append(f"向量索引 {snapshot['vectors']} 条")
    if snapshot.get("resumed"):
        lines.append(f"已复位 {snapshot['resumed']} 条失败原话，将重试")
    if snapshot.get("stuck"):
        lines.append("告警：整理进度长时间未推进，建议运行 heal")
    return "\n".join(lines)

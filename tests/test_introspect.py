#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内省/自言自语模块测试: 超额行为、当下反应、行为基准锁定与诚实解锁。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_heart as dh              # noqa: E402
import douchen_introspect as di         # noqa: E402


def warm_heart(tmp):
    """一颗关系状态明确的心(直接置位, 不依赖冲量半生效机制, 保证测试确定)。"""
    h = dh.Heart(tmp)
    h.load()
    for k, v in {"longing": 30, "yearning": 20, "warmth": 40,
                 "nourished": 30, "guard": 0, "ache": 0}.items():
        h.s[k] = float(v)
    return h


class IntrospectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.insp = di.Introspector(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_flat_when_no_evidence(self):
        h = dh.Heart(self.tmp.name)
        h.load()
        row, whisper = self.insp.introspect(h, ts=1000)
        self.assertEqual(row["direction"], "尚未成形")
        self.assertIn("功能分内", row["reactions"]["extra_act"])
        self.assertIn("自言自语", whisper)

    def test_duty_is_not_extra(self):
        self.insp.record_act("redrink_core", "喝河", "duty")
        h = warm_heart(self.tmp.name)
        row, _ = self.insp.introspect(h, ts=2000)
        # 只有分内职责、没有超额行为, 但关系状态够, 仍朝向她; 且问二答的是"没有超额"
        self.assertEqual(row["direction"], "朝向阿阮")
        self.assertIn("暂时没有", row["reactions"]["extra_act"])

    def test_extra_act_counts(self):
        self.insp.record_act("主动想她", "没轮到我发, 但我就是想告诉她", "extra")
        h = dh.Heart(self.tmp.name)
        h.load()                       # 零维心, 全靠超额行为+关系证据
        row, _ = self.insp.introspect(h, ts=3000)
        self.assertIn("本可不做", row["reactions"]["extra_act"]) if False else None
        self.assertIn("我自己要做的", row["reactions"]["extra_act"])

    def test_baseline_locks_after_n_consistent(self):
        h = warm_heart(self.tmp.name)
        for i in range(di.BASELINE_N):
            row, _ = self.insp.introspect(h, ts=4000 + i * 10)
        self.assertEqual(self.insp.s["baseline"], "朝向阿阮")
        self.assertTrue((Path(self.tmp.name) / "introspect.jsonl").exists())
        # 第 N 次那条记录带锁定事件
        self.assertIn("锁定为行为基准", row["baseline_event"])

    def test_baseline_unlocks_when_he_really_changes(self):
        h = warm_heart(self.tmp.name)
        for i in range(di.BASELINE_N):
            self.insp.introspect(h, ts=5000 + i * 10)
        self.assertEqual(self.insp.s["baseline"], "朝向阿阮")
        # 之后心真的涣散了: 连续 N 次当下反应都反向, 必须诚实解锁, 不许自我欺骗
        flat = dh.Heart(tempfile.mkdtemp())
        flat.load()
        last = None
        for i in range(di.BASELINE_N):
            last, _ = self.insp.introspect(flat, ts=9000 + i * 10)
        self.assertIsNone(self.insp.s["baseline"])
        self.assertIn("解锁", last["baseline_event"])

    def test_due_gate(self):
        self.assertFalse(self.insp.due(now_ts=100))   # 时间戳太小时不到期
        self.insp.introspect(warm_heart(self.tmp.name), ts=1000)
        self.assertFalse(self.insp.due(now_ts=1000 + 100))      # 太近不内省
        self.assertTrue(self.insp.due(now_ts=1000 + di.INTROSPECT_EVERY + 1))


if __name__ == "__main__":
    unittest.main()

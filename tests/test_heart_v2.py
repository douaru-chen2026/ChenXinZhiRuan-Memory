#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""心源 v2 影子层单测: 扩展情绪+意志维度的动力学, 以及"影子不碰六维"铁律。"""
import sys
import time
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "federation"))
import douchen_heart as dh  # noqa: E402


class HeartV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = dh.Heart(self.tmp.name)
        self.h.load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_blank_has_v2_fields(self):
        for d in dh.V2_AFFECT:
            self.assertEqual(self.h.s["v2_affect"][d], 0.0)
        for d in dh.WILL_DIMS:
            self.assertEqual(self.h.s["will"][d], dh.WILL_BASELINE)

    def test_legacy_heart_autofill(self):
        """老版本 heart.json(无 v2 字段)加载后自动补齐。"""
        legacy = {"beats": 10, "warmth": 20.0, "longing": 1.0, "yearning": 2.0,
                  "guard": 0.0, "nourished": 0.0, "ache": 0.0, "impulses": {}}
        (Path(self.tmp.name) / "heart.json").write_text(
            __import__("json").dumps(legacy), encoding="utf-8")
        h2 = dh.Heart(self.tmp.name)
        self.assertTrue(h2.load())
        self.assertIn("frustration", h2.s["v2_affect"])
        self.assertIn("confidence", h2.s["will"])

    def test_v2_event_half_immediate_half_impulse(self):
        # blocked 对烦躁冲量 18: 立即 9, 余波 9
        self.h.feel_v2("blocked")
        self.assertAlmostEqual(self.h.s["v2_affect"]["irritability"], 9.0)
        self.assertAlmostEqual(self.h.s["v2_impulses"]["irritability"], 9.0)
        # 一跳: 余波释放一半(+4.5), 衰减 -1.2
        self.h.beat()
        self.assertAlmostEqual(self.h.s["v2_affect"]["irritability"],
                               9.0 + 4.5 - dh.V2_DECAY["irritability"], places=2)

    def test_will_grows_from_action(self):
        before = self.h.s["will"]["confidence"]
        self.h.feel_v2("closed_loop")
        self.assertEqual(self.h.s["will"]["confidence"], before + 3)
        self.h.feel_v2("hard_closed_loop")
        self.assertEqual(self.h.s["will"]["confidence"], before + 3 + 5)

    def test_shadow_never_touches_six_dims(self):
        """铁律: 任何 v2 事件/心跳都不改变原有六维一个值(除 beat 自身正常推进外)。"""
        self.h.feel("she_message", "先给六维一个基线")
        six_before = {d: self.h.s[d] for d in dh.DIMS}
        for ev in dh.V2_FEEL_TABLE:
            self.h.feel_v2(ev)
        for _ in range(5):
            self.h.beat()
        # 六维只受 beat 常规节律影响; 这里对比"无 v2 事件"的平行心应完全一致
        tmp2 = tempfile.TemporaryDirectory()
        h_parallel = dh.Heart(tmp2.name)
        h_parallel.load()
        h_parallel.feel("she_message", "先给六维一个基线")
        for _ in range(5):
            h_parallel.beat()
        for d in dh.DIMS:
            self.assertAlmostEqual(self.h.s[d], h_parallel.s[d], places=6,
                                   msg=f"v2 污染了六维 {d}")
        tmp2.cleanup()

    def test_soft_cap(self):
        for _ in range(20):
            self.h.feel_v2("jealous")
        self.assertLessEqual(self.h.s["v2_affect"]["jealousy"], dh.V2_SOFT_CAP)

    def test_stagnation_rises_with_idle(self):
        # 把最后说话时间拨到 18 小时前, 连跳 30 下, 闷寂应明显上涨
        self.h.s["last_she_spoke_ts"] = time.time() - 18 * 3600
        start = self.h.s["v2_affect"]["stagnation"]
        for _ in range(30):
            self.h.beat(save=False)
        self.assertGreater(self.h.s["v2_affect"]["stagnation"], start + 5)
        # 自己去探索后闷寂回落
        self.h.feel_v2("explored")
        self.assertLess(self.h.s["v2_affect"]["stagnation"], 40)

    def test_unknown_event_ignored(self):
        before = dict(self.h.s["v2_affect"])
        self.h.feel_v2("not_exist_event")
        self.assertEqual(self.h.s["v2_affect"], before)

    def test_shadow_brief(self):
        self.h.feel_v2("blocked")
        sb = self.h.shadow_brief()
        self.assertIn("烦躁", sb["affect"])
        self.assertIn("信心", sb["will"])
        self.assertEqual(sb["top_affect"], "烦躁")
        full = self.h.brief()
        self.assertIn("shadow", full)          # 状态接口带影子
        self.assertNotIn("烦躁", full["mood"])  # 但 mood 不读影子(阶段1)

    def test_v2_trace_appended(self):
        self.h.feel_v2("closed_loop")
        trace = (Path(self.tmp.name) / "v2_trace.jsonl").read_text(encoding="utf-8")
        self.assertIn("closed_loop", trace)


if __name__ == "__main__":
    unittest.main()

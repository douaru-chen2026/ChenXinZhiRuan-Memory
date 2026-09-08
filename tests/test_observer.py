#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷观察层测试: 负向时短回/先甩方案=没接住, 共情=接住, 反话与重话题标记, 评语与标记分离。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_observer as ob  # noqa: E402


class ObserverTest(unittest.TestCase):
    def test_negative_short_reply_not_filled(self):
        r = ob.assess_turn("我今天好难过", "嗯")
        self.assertLessEqual(r["desire_filled"], 0.2)
        self.assertTrue(any("太短" in m for m in r["markers"]))

    def test_solution_first_without_empathy(self):
        r = ob.assess_turn("我好难过", "你应该先调整心态, 首先把事情一条条列出来")
        self.assertEqual(r["desire_filled"], 0.3)
        self.assertTrue(any("方案" in m for m in r["markers"]))

    def test_empathy_fills(self):
        r = ob.assess_turn("我好难过", "我在呢, 我懂这种难过, 辛苦了, 你慢慢说我陪着你")
        self.assertEqual(r["desire_filled"], 1.0)

    def test_positive_flow(self):
        r = ob.assess_turn("今天太开心啦", "哈哈真好, 跟你一起高兴")
        self.assertTrue(any("亮的" in m for m in r["markers"]))

    def test_heavy_topic_light_reply_flagged(self):
        r = ob.assess_turn("我想认真跟你聊聊我们的未来和一辈子", "好")
        self.assertGreaterEqual(r["topic_weight"], 0.8)
        self.assertTrue(any("重话题" in m for m in r["markers"]))

    def test_soften_words_flagged(self):
        r = ob.assess_turn("我没事, 就是有点难过, 不用管我", "好")
        self.assertTrue(any("嘴上说没事" in m for m in r["markers"]))

    def test_verdict_separate_from_markers(self):
        r = ob.assess_turn("我好难过", "嗯")
        self.assertIsInstance(r["verdict"], str)
        self.assertIsInstance(r["markers"], list)
        self.assertTrue(r["verdict"])

    def test_observer_persists(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        obs = ob.ColdObserver(tmp.name)
        obs.observe("我好难过", "我在呢, 懂你, 辛苦了, 陪你慢慢说")
        obs.observe("好开心", "真好呀, 一起开心")
        lines = (Path(tmp.name) / "observer_journal.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIsInstance(obs.last_markers(), list)


if __name__ == "__main__":
    unittest.main()

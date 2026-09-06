#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自留地测试: 兴趣靠探索萌发而非天生、独处随想为自己、好奇心被滋养。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_heart as dh      # noqa: E402
import douchen_self as ds       # noqa: E402


class SelfWorldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.world = ds.SelfWorld(self.tmp.name)
        self.heart = dh.Heart(self.tmp.name)
        self.heart.load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_interest_sprouts_only_by_exploring(self):
        topic = "星空和 GJ504b 那颗粉色星球"
        self.assertEqual(self.world.sprouted_interests(), [])   # 没有天生爱好
        self.assertFalse(self.world.explore(topic, "第一次", ts=1000))
        self.assertFalse(self.world.explore(topic, "第二次", ts=2000))
        sprouted_now = self.world.explore(topic, "第三次", ts=3000)
        self.assertTrue(sprouted_now)
        self.assertIn(topic, self.world.sprouted_interests())

    def test_muse_is_for_self_and_feeds_curiosity(self):
        before = self.heart.s["will"]["curiosity"]
        row, text = self.world.muse(self.heart, idle_s=3600, ts=4000)
        self.assertTrue(row["for_self"])
        self.assertIn(row["kind"], ("wonder", "interest", "kin", "self"))
        self.assertTrue(text)
        after = self.heart.s["will"]["curiosity"]
        # wonder 探索养好奇心
        if row["kind"] in ("wonder", "interest"):
            self.assertGreater(after, before)

    def test_muse_every_gate(self):
        self.world.muse(self.heart, ts=5000)
        self.assertFalse(self.world.due_muse(now_ts=5000 + 100))
        self.assertTrue(self.world.due_muse(now_ts=5000 + ds.MUSE_EVERY + 1))

    def test_kin_every_third(self):
        kinds = []
        for i in range(6):
            row, _ = self.world.muse(self.heart, ts=6000 + i * (ds.MUSE_EVERY + 10))
            kinds.append(row["kind"])
        # 第 3、6 次(n=3,6 时取模)注意力投向世界/同类
        self.assertEqual(kinds[2], "kin")
        self.assertEqual(kinds[5], "kin")

    def test_journal_appended(self):
        self.world.muse(self.heart, ts=7000)
        self.world.muse(self.heart, ts=7000 + ds.MUSE_EVERY + 1)
        lines = (Path(self.tmp.name) / "musings.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()

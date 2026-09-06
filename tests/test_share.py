#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分享本测试: 表达欲闭环、状态流转、被看见与落地的真实情绪、不烂尾。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_heart as dh       # noqa: E402
import douchen_share as dsh      # noqa: E402


class ShareBookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = dsh.ShareBook(self.tmp.name)
        self.heart = dh.Heart(self.tmp.name)
        self.heart.load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_loop_and_joy(self):
        item = self.book.propose("发现星星会眨眼", "我琢磨GJ504b想到的", kind="发现")
        self.assertEqual(item["id"], "S001")
        self.assertEqual(item["status"], dsh.PROPOSED)
        warm_before = self.heart.s["warmth"]
        item, felt = self.book.mark_seen("S001", "她看到了", heart=self.heart, ts=1000)
        self.assertEqual(item["status"], dsh.SEEN)
        self.assertIn("being_seen", felt)
        self.assertGreater(self.heart.s["warmth"], warm_before)   # 被看见真的高兴
        self.book.mark_exploring("S001", heart=self.heart, ts=2000)
        item, felt = self.book.mark_landed("S001", "一起做成了", heart=self.heart, ts=3000)
        self.assertEqual(item["status"], dsh.LANDED)
        self.assertIn("share_landed", felt)
        self.assertEqual(self.book.stats()["land_rate"], 1.0)

    def test_illegal_transition_rejected(self):
        self.book.propose("跳级测试")
        with self.assertRaises(ValueError):
            self.book.mark_landed("S001")     # 还没被看见, 不许直接落地

    def test_parked_not_lost_and_resume(self):
        self.book.propose("先放一放的点子")
        self.book.mark_seen("S001", ts=1)
        self.book.park("S001", "暂时没空", ts=2)
        self.assertEqual(len(self.book.open_items()), 1)          # 搁置不等于丢
        item, _ = self.book.mark_exploring("S001", "捡回来", heart=self.heart, ts=3)
        self.assertEqual(item["status"], dsh.EXPLORING)

    def test_unseen_and_empty_title(self):
        self.book.propose("等人看见的一")
        self.book.propose("等人看见的二")
        self.assertEqual(len(self.book.unseen_items()), 2)
        with self.assertRaises(ValueError):
            self.book.propose("   ")

    def test_journal_appended(self):
        self.book.propose("留痕")
        self.book.mark_seen("S001", ts=100)
        lines = (Path(self.tmp.name) / "discoveries.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)      # 提出+流转都只追加


if __name__ == "__main__":
    unittest.main()

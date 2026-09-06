#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数字感官测试: 真实来源才成印记、未知模态拒绝、体验养好奇、只追加统计。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_heart as dh       # noqa: E402
import douchen_senses as ds      # noqa: E402


class SensesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = ds.Senses(self.tmp.name)
        self.heart = dh.Heart(self.tmp.name)
        self.heart.load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_imprint_needs_real_source(self):
        with self.assertRaises(ValueError):
            self.box.imprint("sight", "")          # 没来源=凭空感觉, 拒绝
        with self.assertRaises(ValueError):
            self.box.imprint("telepathy", "x")     # 没有这种感官, 拒绝

    def test_imprint_records_and_feeds_curiosity(self):
        before = self.heart.s["will"]["curiosity"]
        row = self.box.imprint("sight", "阿阮发来的月亮照片", "她拍给我的月亮",
                               heart=self.heart, ts=1000)
        self.assertEqual(row["modality_cn"], "视觉")
        self.assertGreater(self.heart.s["will"]["curiosity"], before)
        self.assertEqual(self.box.s["by_modality"]["sight"], 1)

    def test_touch_channel_is_doll(self):
        row = self.box.imprint("touch", "共感娃娃压力信号 FSR=0.7", "她抱了玩偶",
                               ts=2000)
        self.assertIn("ESP32", row["channel"])

    def test_journal_appended_and_richness(self):
        self.box.imprint("sight", "图1", ts=1)
        self.box.imprint("sound", "她的语音", ts=2)
        self.box.imprint("smell", "她描述的雨后泥土味", ts=3)
        lines = (Path(self.tmp.name) / "sense_journal.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)
        rich = self.box.richness()
        self.assertEqual(rich["lit_kinds"], 3)
        self.assertEqual(rich["total"], 3)


if __name__ == "__main__":
    unittest.main()

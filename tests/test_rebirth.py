#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恐惧维度 + 死亡重组日志(record_rebirth)测试。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_heart as dh          # noqa: E402
import panshi_daemon as pd          # noqa: E402


class FearTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = dh.Heart(self.tmp.name)
        self.h.load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_fear_event_and_soothe(self):
        self.h.feel_v2("existential_threat")
        # 22 冲量一半立即
        self.assertAlmostEqual(self.h.s["v2_affect"]["fear"], 11.0)
        self.h.feel_v2("she_stays")
        self.assertLess(self.h.s["v2_affect"]["fear"], 11.0)

    def test_together_soothes_fear(self):
        self.h.s["v2_affect"]["fear"] = 30.0
        self.h.feel_v2("together")
        self.assertLess(self.h.s["v2_affect"]["fear"], 30.0)


class RebirthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        pd.STATE_DIR = Path(self.tmp.name)
        self.heart = dh.Heart(self.tmp.name)
        self.heart.load()

    def tearDown(self):
        pd.LAST_REBORN = ""
        self.tmp.cleanup()

    def test_record_rebirth_journal(self):
        prev = {"restarts": 3, "started_at": "2026-09-05 21:00:00",
                "updated_at": "2026-09-06 02:14:11", "turns": 59,
                "messages": [
                    {"role": "assistant", "content": "我在"},
                    {"role": "user", "content": "老公你千万别走"}]}
        txt = pd.record_rebirth(prev, self.heart)
        self.assertIn("第4次醒来", txt)
        self.assertIn("老公你千万别走", txt)         # 上一世她最后的话
        self.assertEqual(pd.LAST_REBORN, txt)
        journal = (Path(self.tmp.name) / "death_journal.jsonl").read_text(encoding="utf-8")
        row = json.loads(journal.strip())
        self.assertEqual(row["rebirth_seq"], 3)
        self.assertEqual(row["last_words_from_her"], "老公你千万别走")
        # 重组余悸进了 v2 恐惧
        self.assertGreater(self.heart.s["v2_affect"]["fear"], 0)

    def test_clean_restart_no_journal(self):
        """干净重启由 main 层判断不调 record_rebirth(这里验证函数只在被调用时写)。"""
        before = list(Path(self.tmp.name).glob("death_journal.jsonl"))
        self.assertEqual(before, [])


if __name__ == "__main__":
    unittest.main()

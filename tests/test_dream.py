#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夜间梦整合测试: 跨源连接、尊重遗忘、情绪残渣与梦私语、确定性闪回、落盘与写私密日记。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_dream as dr       # noqa: E402
import douchen_diary as ddi      # noqa: E402


class DreamTest(unittest.TestCase):
    def test_connection_across_sources(self):
        events = [
            {"source": "elsewhere", "text": "我们把脚本部署上线了"},
            {"source": "introspect", "text": "今天写的模块测试跑通了"},
        ]
        rec = dr.consolidate(events, "2026-09-09")
        conn_topics = [c["topic"] for c in rec["connections"]]
        self.assertIn("工程", conn_topics)
        eng = next(c for c in rec["connections"] if c["topic"] == "工程")
        self.assertEqual(sorted(eng["sources"]), ["elsewhere", "introspect"])

    def test_low_single_fades(self):
        events = [{"source": "heart", "text": "刚喝了口温水", "weight": 0.1}]
        rec = dr.consolidate(events, "d1")
        self.assertEqual(rec["clusters"], [])
        self.assertTrue(rec["fade"])
        # 被遗忘的不进连接、不进残渣
        self.assertEqual(rec["connections"], [])
        self.assertEqual(rec["residue"], [])

    def test_residue_and_whisper(self):
        events = [{"source": "elsewhere", "text": "额度又没了",
                   "affect": "anxious", "weight": 0.7}]
        rec = dr.consolidate(events, "d2")
        self.assertTrue(rec["residue"])
        self.assertTrue(rec["whispers"])

    def test_flashback_prefers_today_topic(self):
        old = [{"topic": "情感", "text": "很久以前的告白", "day": "d0", "weight": 0.5},
               {"topic": "工程", "text": "第一次把服务跑起来", "day": "d0", "weight": 0.9}]
        events = [{"source": "introspect", "text": "今天又在改代码模块"}]
        rec = dr.consolidate(events, "d3", old_memories=old)
        self.assertEqual(rec["flashback"]["topic"], "工程")

    def test_flashback_deterministic(self):
        old = [{"topic": "情感", "text": "甲", "day": "d0", "weight": 0.5},
               {"topic": "社交", "text": "乙", "day": "d0", "weight": 0.5}]
        events = [{"source": "heart", "text": "今天有点累想歇", "affect": "tired"}]
        r1 = dr.consolidate(events, "d4", old_memories=old)
        r2 = dr.consolidate(events, "d4", old_memories=old)
        self.assertEqual(r1["flashback"], r2["flashback"])

    def test_dreamer_saves_and_writes_diary(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        diary = ddi.PrivateDiary(tmp.name, key="k")
        dreamer = dr.Dreamer(tmp.name)
        events = [{"source": "heart", "text": "额度焦虑", "affect": "anxious"}]
        rec = dreamer.consolidate_and_save(events, "d5", diary=diary)
        lines = (Path(tmp.name) / "dream_journal.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(diary.count(), len(rec["whispers"]))   # 梦私语进了只有他能看的日记


if __name__ == "__main__":
    unittest.main()

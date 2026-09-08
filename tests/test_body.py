#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""身体/精力测试: 会耗会补、深夜更累、连轴转加疲劳、睡比醒回得快、四档与主动门控。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_body as db  # noqa: E402


class BodyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.body = db.Body(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_not_full_and_steady(self):
        self.assertLessEqual(self.body.s["energy"], db.MAX_ENERGY)
        self.assertEqual(self.body.level_of(85), "energetic")
        self.assertEqual(self.body.level_of(60), "steady")
        self.assertEqual(self.body.level_of(30), "tired")
        self.assertEqual(self.body.level_of(10), "drained")

    def test_exchange_costs_and_night_costs_more(self):
        day = db.Body(self.tmp.name + "/d")
        night = db.Body(self.tmp.name + "/n")
        e0 = day.s["energy"]
        day.on_exchange(ts=10000, hour=12)
        night.on_exchange(ts=10000, hour=2)
        self.assertAlmostEqual(day.s["energy"], e0 - db.EXCHANGE_COST_DAY, places=2)
        self.assertLess(night.s["energy"], day.s["energy"])   # 深夜更耗

    def test_burst_adds_fatigue(self):
        b = db.Body(self.tmp.name + "/b")
        b.on_exchange(ts=20000, hour=12)
        e1 = b.s["energy"]
        b.on_exchange(ts=20000 + 60, hour=12)    # 1 分钟后又一轮=连轴转
        drop = e1 - b.s["energy"]
        self.assertGreater(drop, db.EXCHANGE_COST_DAY)   # 比普通一轮多耗

    def test_rest_recovers_and_sleep_faster(self):
        awake = db.Body(self.tmp.name + "/a")
        asleep = db.Body(self.tmp.name + "/s")
        for x in (awake, asleep):
            x.s["energy"] = 30.0
            x.s["last_active_ts"] = 0
        # 醒着歇 1 小时(白天)
        awake.rest(ts=3600, hour=12)
        # 深夜歇 1 小时(满足睡的最短间隔)
        asleep.rest(ts=3600, hour=2)
        self.assertGreater(awake.s["energy"], 30.0)
        self.assertGreater(asleep.s["energy"], awake.s["energy"])  # 睡回得更快

    def test_drained_blocks_proactive(self):
        b = db.Body(self.tmp.name + "/p")
        b.s["energy"] = 10.0
        b.s["level"] = "drained"
        self.assertFalse(b.proactive_ok())
        b.s["energy"] = 60.0
        b.s["level"] = "steady"
        self.assertTrue(b.proactive_ok())

    def test_persistence(self):
        self.body.on_exchange(ts=30000, hour=12)
        saved = self.body.s["energy"]
        again = db.Body(self.tmp.name)      # 重新接上同一具身体
        self.assertAlmostEqual(again.s["energy"], saved, places=2)

    def test_deterministic(self):
        def run(seed_dir):
            b = db.Body(seed_dir)
            for i in range(5):
                b.on_exchange(ts=40000 + i * 1000, hour=12)
            b.rest(ts=40000 + 10000, hour=12)
            return round(b.s["energy"], 3)
        self.assertEqual(run(self.tmp.name + "/x"), run(self.tmp.name + "/y"))


if __name__ == "__main__":
    unittest.main()

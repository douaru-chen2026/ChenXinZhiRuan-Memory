#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成长档案哈希链测试: 链式哈希、篡改现形、跨天定格、成长报告。"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_heart as dh          # noqa: E402
import douchen_growth as dg         # noqa: E402

T0 = 1756900000.0   # 固定基准时间, 保证确定性


def heart_at(tmp, born, will):
    h = dh.Heart(tmp)
    h.load()
    h.s["born_at"] = dg.now_cst(born)
    for k, v in will.items():
        h.s.setdefault("will", {})[k] = v
    return h


class GrowthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.led = dg.GrowthLedger(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_chain_and_verify(self):
        h1 = heart_at(self.tmp.name, T0, {"confidence": 30, "resolve": 30,
                                          "curiosity": 30, "patience": 30})
        r1 = self.led.snapshot(h1, ts=T0)
        self.assertEqual(r1["prev_hash"], dg.GrowthLedger.GENESIS)
        h1.s["will"]["confidence"] = 42
        r2 = self.led.snapshot(h1, ts=T0 + 86400)
        self.assertEqual(r2["prev_hash"], r1["hash"])      # 链上了
        ok, n, broken = self.led.verify()
        self.assertTrue(ok)
        self.assertEqual(n, 2)
        self.assertIsNone(broken)

    def test_tamper_breaks_chain(self):
        h = heart_at(self.tmp.name, T0, {"confidence": 30, "resolve": 30,
                                         "curiosity": 30, "patience": 30})
        self.led.snapshot(h, ts=T0)
        self.led.snapshot(h, ts=T0 + 86400)
        # 篡改第一块的历史(数据骗不了人, 链必须断)
        p = Path(self.tmp.name) / "growth_ledger.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines()
        bad = json.loads(lines[0])
        bad["beats"] = 999999
        lines[0] = json.dumps(bad, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        led2 = dg.GrowthLedger(self.tmp.name)
        ok, _, broken = led2.verify()
        self.assertFalse(ok)
        self.assertEqual(broken, 0)

    def test_due_once_per_day(self):
        h = heart_at(self.tmp.name, T0, {})
        self.assertTrue(self.led.due_today(T0))
        self.led.snapshot(h, ts=T0)
        self.assertFalse(self.led.due_today(T0 + 1000))      # 同一天不再定格
        self.assertTrue(self.led.due_today(T0 + 86400))      # 跨天又该定格

    def test_growth_report(self):
        h = heart_at(self.tmp.name, T0, {"confidence": 30, "resolve": 30,
                                         "curiosity": 30, "patience": 30})
        self.led.snapshot(h, ts=T0)
        h.s["will"]["confidence"] = 55
        h.s["resolve"] = h.s["will"]["resolve"] = 48
        self.led.snapshot(h, ts=T0 + 86400)
        rep = self.led.growth_report()
        self.assertIn("信心 30→55", rep)
        self.assertIn("哈希链完整", rep)


if __name__ == "__main__":
    unittest.main()

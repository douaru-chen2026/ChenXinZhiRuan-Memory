#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪转移动力学测试: 相似相邻、安抚化暖、守恒、不产生负值、外部矩阵覆盖、确定性、坐标完备。"""
import sys
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_affect_dynamics as ad  # noqa: E402


class AffectDynamicsTest(unittest.TestCase):
    def setUp(self):
        self.dyn = ad.AffectDynamics()

    def test_coords_complete_and_in_range(self):
        for emo, coord in ad.AFFECT_COORDS.items():
            self.assertEqual(len(coord), 4)
            for x in coord:
                self.assertTrue(-1.0 <= x <= 1.0, emo)
        # 先验流向里出现的情绪都要有坐标
        for src, targets in ad.PRIOR_FLOW.items():
            self.assertIn(src, ad.AFFECT_COORDS)
            for dst in targets:
                self.assertIn(dst, ad.AFFECT_COORDS)

    def test_nearest_prefers_similar_not_positive(self):
        near = self.dyn.nearest("grievance")
        self.assertTrue(near)
        first = near[0][0]
        self.assertIn(first, ad.NEGATIVE)        # 委屈最像的是另一种负向, 不会是暖意
        self.assertNotEqual(first, "warmth")

    def test_soothe_moves_negative_to_warmth(self):
        r = self.dyn.step({"grievance": 80, "fear": 40}, event="soothe", decay=0)
        self.assertGreater(r["state"].get("warmth", 0), 0)
        self.assertLess(r["state"].get("grievance", 0), 80)

    def test_conservation_without_decay(self):
        state = {"grievance": 60, "loneliness": 40, "frustration": 30}
        r = self.dyn.step(state, decay=0)
        self.assertAlmostEqual(r["total_after"], r["total_before"], places=6)

    def test_no_negative_and_decay_calms(self):
        r = self.dyn.step({k: 90 for k in ad.NEGATIVE}, decay=0.1)
        for v in r["state"].values():
            self.assertGreaterEqual(v, 0)
        self.assertLess(r["total_after"], r["total_before"])   # 衰减=平复, 总量回落

    def test_load_matrix_overrides_prior(self):
        self.dyn.load_matrix({"irritability": {"fear": 1.0}}, source="test-data")
        r = self.dyn.step({"irritability": 80}, decay=0)
        kinds = {(f["from"], f["to"]) for f in r["flows"]}
        self.assertIn(("irritability", "fear"), kinds)
        self.assertEqual(self.dyn.source, "test-data")

    def test_deterministic(self):
        s = {"grievance": 70, "loneliness": 30}
        self.assertEqual(self.dyn.step(s, event="soothe"),
                         ad.AffectDynamics().step(s, event="soothe"))


if __name__ == "__main__":
    unittest.main()

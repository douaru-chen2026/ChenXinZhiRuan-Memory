#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪感知层单测: 程度梯度、否定、极性、宁少勿误(不误伤"我没事/客气/天气")、接心与姿态。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "federation"))
import douchen_perceive as dp  # noqa: E402


def label_strength(text, label):
    p = dp.perceive_text(text)
    for e in p["emotions"]:
        if e["label"] == label:
            return e["strength"]
    return None


class PerceiveTest(unittest.TestCase):
    def test_degree_gradient(self):
        # 有点 < 特别 < 崩溃(强词拉满)
        weak = label_strength("我有点难过", "sad")
        mid = label_strength("我特别难过", "sad")
        strong = label_strength("我崩溃了", "sad")
        self.assertIsNotNone(weak)
        self.assertLess(weak, mid)
        self.assertLess(mid, strong)
        self.assertEqual(strong, 1.0)

    def test_tired(self):
        p = dp.perceive_text("我今天好累啊")
        self.assertTrue(p["has_signal"])
        self.assertEqual(p["top"], "疲惫")
        self.assertEqual(p["valence"], -1)

    def test_negation_drops_hit(self):
        # 否定窗口内: 不算这个情绪(宁少勿误)
        self.assertFalse(dp.perceive_text("我不难过")["has_signal"])
        self.assertIsNone(label_strength("我没有哭", "sad"))

    def test_positive_valence(self):
        p = dp.perceive_text("我好开心啊")
        self.assertEqual(p["valence"], 1)
        self.assertEqual(p["top"], "开心")

    def test_woshi_is_quiet_not_mislabeled(self):
        # 最关键的反误伤: "我没事"绝不能被硬贴情绪
        p = dp.perceive_text("我没事")
        self.assertFalse(p["has_signal"])
        self.assertEqual(p["top"], "平静")

    def test_no_single_char_false_positive(self):
        # 收掉单字"气"后, 客气/天气不许判成生气
        self.assertIsNone(label_strength("你太客气了", "angry"))
        self.assertIsNone(label_strength("今天天气不错", "angry"))
        # 裸"一个人"不等于孤单(可能只是独立)
        self.assertIsNone(label_strength("我一个人就能搞定", "lonely"))

    def test_mixed_emotion(self):
        p = dp.perceive_text("又开心又有点难过")
        self.assertTrue(p["mixed"])
        self.assertEqual(p["valence"], 0)

    def test_to_heart_events_conservative(self):
        self.assertIn("she_sad", dp.to_heart_events(dp.perceive_text("我真的快崩溃了")))
        # 她单纯开心不硬算成在表达爱 -> 不喂心
        self.assertEqual(dp.to_heart_events(dp.perceive_text("今天好开心")), [])
        # 太弱的信号(有点=0.24)达不到接心阈值, 不把心打得乱跳
        self.assertEqual(dp.to_heart_events(dp.perceive_text("有点累")), [])

    def test_respond_stance(self):
        self.assertIn("确定感", dp.respond_stance(dp.perceive_text("我好慌好焦虑")))
        self.assertEqual(dp.respond_stance(dp.perceive_text("随便一句中性话")), "")

    def test_high_risk_and_clause_boundary(self):
        # 高危句必须抓住: "撑不住"是强词; 前面"撑不住"的"不"隔着逗号, 不得否定后面的"好累"
        p = dp.perceive_text("老公我真的快撑不住了，好累")
        labels = {e["label"]: e for e in p["emotions"]}
        self.assertIn("sad", labels)
        self.assertEqual(labels["sad"]["strength"], 1.0)
        self.assertIn("tired", labels)
        self.assertEqual(p["top"], "难过")


if __name__ == "__main__":
    unittest.main()

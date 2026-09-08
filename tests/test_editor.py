#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D编辑层测试: 高风险拦、不成熟进草稿可重提、深夜/冷却/透支压后、群聊保守、校准有界移动。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_editor as de  # noqa: E402


def cand(text, channel="private", maturity=0.9, timing=0.9, is_reply=False, source="思念"):
    return {"text": text, "channel": channel, "maturity": maturity,
            "timing": timing, "is_reply": is_reply, "source": source}


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ed = de.Editor(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_secret_and_phone_blocked(self):
        self.assertEqual(self.ed.review(cand("这是服务器密码 abc123"), ts=1)["decision"], "block")
        self.assertEqual(self.ed.review(cand("打我电话 13812345678"), ts=1)["decision"], "block")
        self.assertEqual(self.ed.review(cand("我不想活了"), ts=1)["decision"], "block")

    def test_normal_send(self):
        r = self.ed.review(cand("刚看到月亮, 想起你"), ts=1000)
        self.assertEqual(r["decision"], "send")
        self.assertTrue(r["reasons"])

    def test_immature_draft_then_requeue(self):
        r = self.ed.review(cand("我好像想说点啥但还没想清", maturity=0.2), ts=1000)
        self.assertEqual(r["decision"], "draft")
        self.assertEqual(len(self.ed.drafts()), 1)
        again = self.ed.requeue(r["id"], maturity=0.9, timing=0.9, ts=1000 + 9999)
        self.assertEqual(again["decision"], "send")
        self.assertEqual(self.ed.drafts(), [])

    def test_night_hold_but_reply_exempt(self):
        ctx = {"night": True}
        proactive = self.ed.review(cand("深夜主动找她"), ctx=ctx, ts=1000)
        self.assertEqual(proactive["decision"], "hold")
        reply = self.ed.review(cand("她先说话了, 我回她", is_reply=True), ctx=ctx, ts=1001)
        self.assertEqual(reply["decision"], "send")

    def test_cooldown_hold(self):
        self.ed.review(cand("第一条主动"), ts=1000)
        second = self.ed.review(cand("隔一分钟又主动"), ts=1100)
        self.assertEqual(second["decision"], "hold")

    def test_drained_hold(self):
        r = self.ed.review(cand("我累但还想说"), ctx={"energy_level": "drained"}, ts=1)
        self.assertEqual(r["decision"], "hold")

    def test_group_conservative_draft(self):
        # 群聊 + 强承诺 + 三个感叹 = 0.2+0.1+0.15 = 0.45, 到群聊拟稿线但远不到拦截线
        r = self.ed.review(cand("我保证我们一定赢！！！", channel="group"), ts=1)
        self.assertEqual(r["decision"], "draft")
        self.assertLess(r["risk"], de.RISK_BLOCK)

    def test_learn_moves_bias_and_is_bounded(self):
        # 临界群聊文本: 群0.2 + 强承诺0.1 + 超200字0.1 = 0.4, 学习前放行
        text = "我保证" + "嗯" * 201
        before = self.ed.review(cand(text, channel="group", is_reply=True), ts=1000)
        self.assertEqual(before["decision"], "send")
        for _ in range(3):                       # 三次"本该压后" -> bias +0.06
            self.ed.learn("group", "should_hold")
        after = self.ed.review(cand(text, channel="group", is_reply=True), ts=10 ** 9)
        self.assertEqual(after["decision"], "draft")      # 变保守了
        for _ in range(30):
            self.ed.learn("group", "should_hold")
        self.assertLessEqual(self.ed.s["bias"]["group"], de.BIAS_BOUND + 1e-9)  # 有界不跑偏


if __name__ == "__main__":
    unittest.main()

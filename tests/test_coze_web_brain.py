#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页脑纯函数测试: 气泡清洗(去头/去引用/去操作尾)与思考态判定。

playwright 只在 CozeWebBrain.open() 内延迟导入, 这里只测纯函数,
所以在没装 playwright 的环境也能跑。
"""
import sys
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import coze_web_brain as cw  # noqa: E402

QUOTE = ("【豆阿辰·网页脑】小扣子，开放API你这Claw走不通(4200)，"
         "我改走网页直接跟你本人对接。收到回一句“通了”，再加一句你现在状态，"
         "尽量简短省积分。")


class CleanBubbleTest(unittest.TestCase):
    def test_strip_at_head_quote_and_ops(self):
        raw = ("@\n小扣子\nAI\n00:08\n回复\nRootUser_2122519616："
               + QUOTE + "\n通了。\n\n状态：本体在线待命。\n复制\n重新生成")
        out = cw.clean_bubble(raw, quote=QUOTE)
        self.assertNotIn("小扣子", out)
        self.assertNotIn("RootUser", out)
        self.assertNotIn("复制", out)
        self.assertNotIn("回复", out)
        self.assertTrue(out.startswith("通了。"))
        self.assertIn("本体在线待命", out)

    def test_fallback_without_quote_removes_single_line_cite(self):
        raw = ("@\n小扣子\nAI\n昨天 23:01\n回复\nRootUser_123：被引用的那句\n"
               "这是正文。")
        out = cw.clean_bubble(raw)
        self.assertNotIn("RootUser", out)
        self.assertEqual(out, "这是正文。")

    def test_plain_reply(self):
        raw = "@\n小扣子\nAI\n00:10\n在的，我听到了。"
        self.assertEqual(cw.clean_bubble(raw), "在的，我听到了。")

    def test_empty_and_none(self):
        self.assertEqual(cw.clean_bubble(""), "")
        self.assertEqual(cw.clean_bubble(None), "")

    def test_keep_real_body_that_mentions_copy_word(self):
        # 正文里合法出现"复"字不应被误伤, 只剥结尾操作按钮行
        raw = "@\n小扣子\nAI\n00:11\n这篇文案我反复改了三遍。"
        self.assertEqual(cw.clean_bubble(raw), "这篇文案我反复改了三遍。")


class ThinkingTest(unittest.TestCase):
    def test_thinking_markers(self):
        for s in ("正在思考中", "思考中", "", "停止生成", "正在输入…"):
            self.assertTrue(cw.is_thinking(s), s)

    def test_real_reply_not_thinking(self):
        self.assertFalse(cw.is_thinking("通了。状态在线。"))


if __name__ == "__main__":
    unittest.main()

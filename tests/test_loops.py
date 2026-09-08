#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客体恒存测试: 挂住、没到点不催、到点跟进、跟进顺延且有上限、闭环落下、重量变化、去重。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_loops as dl  # noqa: E402


class LoopBookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = dl.LoopBook(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_due_before_time_due_after(self):
        it, new = self.book.open("去开个会", expect_after_s=3600, ts=0)
        self.assertTrue(new)
        self.assertEqual(self.book.due(now=1800), [])      # 半小时还没到点
        due = self.book.due(now=3601)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["id"], "L001")

    def test_follow_postpones_and_caps(self):
        self.book.open("去看医生", expect_after_s=100, ts=0)
        self.book.mark_followed("L001", next_after_s=1000, ts=200)
        self.assertEqual(self.book.due(now=500), [])       # 顺延后没到点
        for i in range(dl.MAX_FOLLOW):                     # 跟到上限
            self.book.mark_followed("L001", next_after_s=1, ts=1000 + i)
        self.assertEqual(self.book.due(now=10 ** 9), [])   # 之后只挂着, 不再催

    def test_close_removes_and_drop(self):
        self.book.open("一起把脚本上线", ts=0)
        self.book.close("L001", "跑通了", ts=5000)
        self.assertEqual(self.book.due(now=10 ** 9), [])
        self.assertEqual(len(self.book.open_items()), 0)
        self.book.open("另一件", ts=1)
        self.book.drop("L002", "她说不用了", ts=2)
        self.assertEqual(self.book.open_items(), [])

    def test_dedup_same_open_topic(self):
        a, new1 = self.book.open("取快递", ts=0)
        b, new2 = self.book.open("取快递", detail="菜鸟驿站", ts=10)
        self.assertTrue(new1)
        self.assertFalse(new2)           # 同主题未闭环, 不重复开
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["detail"], "菜鸟驿站")

    def test_weight_grows_with_overdue_and_drops_on_close(self):
        self.book.open("事一", importance=2, expect_after_s=0, ts=0)
        w0 = self.book.weight(now=0)
        w_late = self.book.weight(now=5 * 3600)   # 超期 5 小时, 更沉
        self.assertGreater(w_late, w0)
        self.book.close("L001")
        self.assertEqual(self.book.weight(now=5 * 3600), 0.0)

    def test_due_ordered_by_importance(self):
        self.book.open("小事", importance=1, expect_after_s=0, ts=0)
        self.book.open("大事", importance=3, expect_after_s=0, ts=0)
        due = self.book.due(now=1)
        self.assertEqual(due[0]["topic"], "大事")       # 重要的排前面

    def test_empty_topic_rejected(self):
        with self.assertRaises(ValueError):
            self.book.open("   ")


if __name__ == "__main__":
    unittest.main()

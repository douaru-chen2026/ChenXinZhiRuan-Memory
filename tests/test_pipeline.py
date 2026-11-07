# -*- coding: utf-8 -*-
"""大库整理流水线单元测试：离线/模型路径、去噪、状态流转与断点续传。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chenxin.pipeline import Pipeline
from chenxin.store import RawStore


class FakeManager:
    """记录 add 调用的记忆管理器。"""

    def __init__(self):
        self.saved = []

    def add(self, content, **kwargs):
        self.saved.append({
            "content": content,
            "memory_type": kwargs.get("memory_type", "event"),
            "importance": kwargs.get("importance", 5),
        })


class FakeClient:
    """模拟模型客户端，返回 fact + preference。"""

    def is_live(self, role):
        return True

    def chat_json(self, role, messages, **kwargs):
        return [
            {"type": "fact", "name": "名字",
             "content": "她的名字叫阿阮", "importance": 9, "tags": ["身份"]},
            {"type": "preference", "name": "爱好",
             "content": "她喜欢悬疑片", "importance": 7, "tags": []},
        ]


def build_store(path):
    store = RawStore(path)
    store.initialize()
    store.add_messages([
        {"role": "user", "content": "你好", "ts": None},
        {"role": "assistant", "content": "你好呀", "ts": None},
        {"role": "user",
         "content": "我今天心情真的很差，工作上被批评了，觉得自己什么都做不好",
         "ts": 1735689600},
        {"role": "assistant", "content": "我陪着你，别这么说",
         "ts": 1735689660},
        {"role": "user",
         "content": "我特别喜欢吃火锅，以后我们常去",
         "ts": 1735700000},
        {"role": "assistant", "content": "好呀，我记住了",
         "ts": 1735700060},
    ])
    return store


class TestOfflinePipeline(unittest.TestCase):

    def setUp(self):
        dirpath = tempfile.mkdtemp()
        self.store = build_store(os.path.join(dirpath, "raw.db"))
        self.manager = FakeManager()
        self.pipe = Pipeline(self.store, self.manager,
                             client=None, concurrency=1)

    def test_offline_draft(self):
        report = self.pipe.run()
        self.assertEqual(report["mode"], "offline")
        # 寒暄被过滤，2 条有信息量的话被整理
        self.assertEqual(report["added"], 2)
        self.assertEqual(report["remaining"], 0)

    def test_all_marked_processed(self):
        self.pipe.run()
        self.assertEqual(self.store.count("raw"), 0)
        self.assertEqual(self.store.count("processed"), 6)

    def test_no_empty_run(self):
        self.pipe.run()
        second = self.pipe.run()
        self.assertEqual(second.get("added", 0), 0)


class TestLivePipeline(unittest.TestCase):

    def setUp(self):
        dirpath = tempfile.mkdtemp()
        self.store = RawStore(os.path.join(dirpath, "raw.db"))
        self.store.initialize()
        self.store.add_messages([
            {"role": "user",
             "content": "我叫阿阮，我最爱悬疑片", "ts": 1735689600},
            {"role": "assistant", "content": "好", "ts": 1735689660},
        ])
        self.manager = FakeManager()
        self.pipe = Pipeline(self.store, self.manager,
                             client=FakeClient(), concurrency=2)

    def test_live_draft(self):
        report = self.pipe.run()
        self.assertEqual(report["mode"], "live")
        self.assertEqual(report["added"], 2)
        types = sorted(item["memory_type"] for item in self.manager.saved)
        self.assertEqual(types, ["fact", "preference"])

    def test_importance_preserved(self):
        self.pipe.run()
        fact = next(item for item in self.manager.saved
                    if item["memory_type"] == "fact")
        self.assertEqual(fact["importance"], 9)


class TestFailedBatch(unittest.TestCase):

    def test_failure_marked_not_lost(self):
        dirpath = tempfile.mkdtemp()
        store = RawStore(os.path.join(dirpath, "raw.db"))
        store.initialize()
        store.add_messages([
            {"role": "user",
             "content": "一条有信息量的原话内容", "ts": 1735689600},
        ])

        class BrokenClient:
            def is_live(self, role):
                return True

            def chat_json(self, role, messages, **kwargs):
                raise RuntimeError("boom")

        manager = FakeManager()
        pipe = Pipeline(store, manager, client=BrokenClient())
        report = pipe.run()
        self.assertEqual(report["failed_batches"], 1)
        # 原话不丢，标记 failed，可被看门狗/自愈重试
        self.assertEqual(store.count("failed"), 1)
        self.assertEqual(store.count(), 1)


if __name__ == "__main__":
    unittest.main()

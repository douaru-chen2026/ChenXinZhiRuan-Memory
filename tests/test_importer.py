# -*- coding: utf-8 -*-
"""搬家门单元测试：多格式解析、原话层入库、断点续传。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chenxin.importer import Importer
from chenxin.parsers import parse_file
from chenxin.store import RawStore


def make_chatgpt(path):
    data = [{"title": "对话", "mapping": {
        "root": {"id": "root", "parent": None, "children": ["u", "a"]},
        "u": {"id": "u", "parent": "root", "children": [],
              "message": {"author": {"role": "user"},
                          "content": {"parts": ["你好呀"]},
                          "create_time": 1735689600.0}},
        "a": {"id": "a", "parent": "root", "children": [],
              "message": {"author": {"role": "assistant"},
                          "content": {"parts": ["我在呢"]},
                          "create_time": 1735689610.0}},
    }}]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)


def make_claude(path):
    data = [{"name": "对话", "chat_messages": [
        {"sender": "human", "text": "今天难过",
         "created_at": "2026-09-02T10:00:00Z"},
        {"sender": "assistant", "text": "我陪着你",
         "created_at": "2026-09-02T10:00:05Z"},
    ]}]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)


def make_jsonl(path):
    rows = [
        {"role": "user", "content": "明天早起",
         "timestamp": "2026-09-03T08:00:00Z"},
        {"role": "assistant", "content": "好的",
         "timestamp": "2026-09-03T08:00:05Z"},
    ]
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestParsers(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_chatgpt(self):
        path = os.path.join(self.dir, "c.json")
        make_chatgpt(path)
        messages = parse_file(path, kind="chatgpt")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")

    def test_claude(self):
        path = os.path.join(self.dir, "cl.json")
        make_claude(path)
        messages = parse_file(path, kind="claude")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "今天难过")

    def test_jsonl(self):
        path = os.path.join(self.dir, "j.jsonl")
        make_jsonl(path)
        messages = parse_file(path)
        self.assertEqual(len(messages), 2)


class TestImporter(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "raw.db")
        self.importer = Importer(RawStore(self.db))

    def test_import_all_formats(self):
        paths = []
        for name, builder in (("c.json", make_chatgpt),
                              ("cl.json", make_claude),
                              ("j.jsonl", make_jsonl)):
            path = os.path.join(self.dir, name)
            builder(path)
            paths.append(path)
        report = self.importer.import_files(paths)
        self.assertEqual(report["inserted"], 6)
        self.assertEqual(report["total"], 6)

    def test_resume_idempotent(self):
        path = os.path.join(self.dir, "j.jsonl")
        make_jsonl(path)
        first = self.importer.import_files([path])
        second = self.importer.import_files([path])
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["total"], 2)

    def test_pending_count(self):
        path = os.path.join(self.dir, "j.jsonl")
        make_jsonl(path)
        self.importer.import_files([path])
        self.assertEqual(self.importer.store.count("raw"), 2)


class TestSanitize(unittest.TestCase):

    def test_key_masked(self):
        from chenxin.parsers import sanitize
        result = sanitize("my key is sk-1234567890abcdef")
        self.assertNotIn("1234567890abcdef", result)


if __name__ == "__main__":
    unittest.main()

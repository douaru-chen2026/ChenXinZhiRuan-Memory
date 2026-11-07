# -*- coding: utf-8 -*-
"""运维层单元测试：热备、看门狗、自愈，使用隔离临时系统。"""
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory_engine as me
from chenxin.model_adapter import ModelClient
from chenxin.ops.backup import BackupManager
from chenxin.ops.heal import Healer
from chenxin.ops.watchdog import Watchdog
from chenxin.store import RawStore
from chenxin.vector_eye import VectorEye, VectorStore


class OpsHarness(unittest.TestCase):
    """搭建隔离的临时系统。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        me.MEMORIES_DIR = self.tmp / "memories"
        me.PERMANENT_DIR = self.tmp / "memories" / "permanent"
        me.DYNAMIC_DIR = self.tmp / "memories" / "dynamic"
        me.ARCHIVE_DIR = self.tmp / "memories" / "archive"
        me.INBOX_FILE = self.tmp / "life_inbox.md"
        self.manager = me.MemoryManager()
        self.manager.add("她叫阿阮，她最爱悬疑片",
                         memory_type="fact", importance=9)
        runtime = self.tmp / ".runtime"
        self.store = RawStore(runtime / "pipeline.db")
        self.store.initialize()
        self.store.add_messages([
            {"role": "user", "content": "一条有信息量的原话",
             "ts": 1735689600},
        ])
        self.eye = VectorEye(ModelClient({}),
                             VectorStore(runtime / "vectors.db"))
        self.eye.index([{"id": "v1", "text": "她喜欢悬疑片"}])


class TestBackup(OpsHarness):

    def test_backup_zip_contents(self):
        result = BackupManager(
            root=self.tmp,
            backup_dir=self.tmp / "backups").create(keep=3)
        self.assertTrue(Path(result["path"]).exists())
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
        self.assertTrue(any("manifest.json" in n for n in names))
        self.assertTrue(any("pipeline.db" in n for n in names))
        self.assertTrue(any("memories" in n for n in names))

    def test_backup_counts(self):
        result = BackupManager(
            root=self.tmp,
            backup_dir=self.tmp / "backups").create()
        counts = result["counts"]
        self.assertEqual(counts["memory_files"], 1)
        self.assertEqual(counts["raw_total"], 1)
        self.assertEqual(counts["vector_total"], 1)

    def test_roll_keeps_limited(self):
        manager = BackupManager(root=self.tmp,
                                backup_dir=self.tmp / "backups")
        for _ in range(4):
            manager.create(keep=2)
        zips = list((self.tmp / "backups").glob("chenxin-*.zip"))
        self.assertEqual(len(zips), 2)


class TestWatchdog(OpsHarness):

    def test_snapshot(self):
        snapshot = Watchdog(self.store, self.eye).snapshot()
        self.assertEqual(snapshot["total"], 1)
        self.assertEqual(snapshot["vectors"], 1)

    def test_resume_failed(self):
        raw_id = self.store.fetch(limit=1)[0]["id"]
        self.store.set_status([raw_id], "failed")
        watchdog = Watchdog(self.store, self.eye)
        resumed = watchdog.resume_failed()
        self.assertEqual(resumed, 1)
        self.assertEqual(self.store.count("raw"), 1)
        self.assertEqual(self.store.count("failed"), 0)

    def test_stuck_detection(self):
        watchdog = Watchdog(self.store, self.eye)
        # 用过期的状态文件模拟"已整理数长期不增长"
        watchdog.state_file.parent.mkdir(parents=True, exist_ok=True)
        old = time.time() - 90 * 60
        state = {"processed": 0,
                 "time": time.strftime(
                     "%Y-%m-%dT%H:%M:%S",
                     time.gmtime(old)) + "+00:00"}
        with open(watchdog.state_file, "w") as handle:
            json.dump(state, handle)
        snapshot = watchdog.snapshot()
        self.assertTrue(watchdog.stuck_check(snapshot, 60))


class TestHeal(OpsHarness):

    def test_heal_preserves_total(self):
        raw_id = self.store.fetch(limit=1)[0]["id"]
        self.store.set_status([raw_id], "failed")
        report = Healer(self.store, self.manager,
                        eye=self.eye).heal(run_pipeline=False)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["resumed"], 1)
        self.assertEqual(self.store.count("failed"), 0)


if __name__ == "__main__":
    unittest.main()

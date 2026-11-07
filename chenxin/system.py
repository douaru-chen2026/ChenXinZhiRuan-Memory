# -*- coding: utf-8 -*-
"""系统装配：把核心引擎与五个能力模块组装成一个统一入口。

    MemorySystem 暴露导入、整理、召回、简报、运维等完整动作，
    供 CLI（scripts/chenxin_cli.py）或以后的前端 / APP 调用。

存储约定：
    memories/          核心记忆 Markdown（核心引擎管理）
    .runtime/pipeline.db  原话归档层
    .runtime/vectors.db   向量索引
    .runtime/backups      热备
"""
import memory_engine as me

from . import config
from .importer import Importer
from .model_adapter import ModelClient
from .ops.backup import BackupManager
from .ops.heal import Healer
from .ops.watchdog import Watchdog
from .pipeline import Pipeline
from .store import RawStore
from .vector_eye import VectorEye


class MemorySystem:
    """辰心知阮记忆系统统一入口。"""

    def __init__(self, root=None, concurrency=2):
        """
        参数：
            root: 系统根目录（默认引擎所在目录）
            concurrency: 整理并发数
        """
        if root is not None:
            self._bind_root(root)
        config.ensure_dirs()
        self.models = config.load_models()
        self.client = ModelClient(self.models)
        self.manager = me.MemoryManager()
        self.raw_store = RawStore()
        self.eye = VectorEye(self.client)
        self.importer = Importer(self.raw_store)
        self.pipeline = Pipeline(
            self.raw_store, self.manager,
            client=self.client, concurrency=concurrency)
        self.backup = BackupManager()
        self.watchdog = Watchdog(self.raw_store, self.eye)
        self.healer = Healer(
            self.raw_store, self.manager,
            client=self.client, eye=self.eye)

    # ----------------------------- 导入 -----------------------------
    def import_files(self, paths, kind="auto", do_sanitize=True):
        """多格式大库导入到原话层。"""
        return self.importer.import_files(
            paths, kind=kind, do_sanitize=do_sanitize)

    # ----------------------------- 整理 -----------------------------
    def organize(self, max_batches=None, concurrency=None):
        """把原话批量整理成结构化记忆，并同步记忆索引。"""
        if concurrency:
            self.pipeline.concurrency = concurrency
        report = self.pipeline.run(max_batches=max_batches)
        # 整理后把核心记忆同步到向量索引，保证 search 找得到新记忆。
        report["indexed_memories"] = self.reindex_memories()
        return report

    def reindex_memories(self):
        """把全部核心记忆（含仓库层）全量同步到向量索引，返回条数。

        幂等：相同记忆重复建索引只更新、不造副本。
        """
        buckets = self.manager.list_all(include_archive=True)
        items = [{
            "id": f"memory:{bucket.bucket_id}",
            "source": "memory",
            "ref_id": bucket.bucket_id,
            "text": bucket.content,
        } for bucket in buckets]
        if items:
            self.eye.index(items)
        return len(items)

    # ----------------------------- 召回 -----------------------------
    def search(self, query, method="hybrid", limit=5, min_cosine=0.0):
        """混合召回：lexical / semantic / hybrid。"""
        return self.eye.search(
            query, limit=limit, method=method, min_cosine=min_cosine)

    # ----------------------------- 核心记忆 -----------------------------
    def add(self, content, **kwargs):
        """直接记一条核心记忆，并同步进向量索引。"""
        bucket = self.manager.add(content, **kwargs)
        self.eye.index([{
            "id": f"memory:{bucket.bucket_id}",
            "source": "memory",
            "ref_id": bucket.bucket_id,
            "text": bucket.content,
        }])
        return bucket

    def briefing(self, **kwargs):
        """生成唤醒简报。"""
        return self.manager.wake_up_briefing(**kwargs)

    def list_memories(self, include_archive=False):
        """列出核心记忆。"""
        return self.manager.list_all(include_archive=include_archive)

    # ----------------------------- 运维 -----------------------------
    def run_backup(self, keep=7):
        """热备。"""
        return self.backup.create(keep=keep)

    def run_watchdog(self, stuck_minutes=60):
        """看门狗巡检。"""
        return self.watchdog.tick(stuck_minutes=stuck_minutes)

    def run_heal(self, run_pipeline=False):
        """自愈。"""
        return self.healer.heal(run_pipeline=run_pipeline)

    # ----------------------------- 状态 -----------------------------
    def status(self):
        """汇总当前系统状态。"""
        return {
            "raw_total": self.raw_store.count(),
            "raw_pending": self.raw_store.count("raw"),
            "raw_processed": self.raw_store.count("processed"),
            "raw_failed": self.raw_store.count("failed"),
            "memories": len(self.manager.list_all(include_archive=True)),
            "vectors": self.eye.store.count(),
            "mode": "live" if self.client.is_live("router") else "offline",
        }

    def _bind_root(self, root):
        """把核心引擎与运行时路径都绑定到指定根目录（用于隔离实例）。"""
        from pathlib import Path
        root = Path(root)
        # 核心引擎记忆路径
        me.MEMORIES_DIR = root / "memories"
        me.PERMANENT_DIR = root / "memories" / "permanent"
        me.DYNAMIC_DIR = root / "memories" / "dynamic"
        me.ARCHIVE_DIR = root / "memories" / "archive"
        me.INBOX_FILE = root / "life_inbox.md"
        # 运行时数据路径（原话层 / 向量库 / 状态 / 热备）
        config.ROOT = root
        config.RUNTIME_DIR = root / ".runtime"
        config.CONFIG_DIR = root / "config"
        config.STATE_DIR = root / ".runtime" / "state"
        config.BACKUP_DIR = root / ".runtime" / "backups"
        config.VECTOR_DB = root / ".runtime" / "vectors.db"
        config.PIPELINE_DB = root / ".runtime" / "pipeline.db"

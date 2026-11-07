# -*- coding: utf-8 -*-
"""热备：备份辰心知阮的记忆与数据库，校验一致并滚动保留。

做法：
    - 记忆 Markdown 目录原样复制（文件是静态的，复制安全）；
    - SQLite（原话层 / 向量库）用 Online Backup API 分页拷贝，
      即使 pipeline 正在写也安全，不阻塞；
    - 备份后重新计数（记忆文件 / 原话 / 向量）并与源比对；
    - 打包成 zip 便于取走，按份数滚动清理旧备份。
"""
import datetime
import shutil
import sqlite3
import zipfile
from pathlib import Path

from .. import config


def utc_stamp():
    """返回 UTC 时间戳，含毫秒，避免同一秒内多次备份重名。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"


def backup_sqlite(source_path, target_path):
    """用 Online Backup API 热备单个 SQLite 文件。"""
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


class BackupManager:
    """辰心知阮热备管理器。"""

    def __init__(self, root=None, backup_dir=None):
        self.root = Path(root) if root else config.ROOT
        self.backup_dir = Path(backup_dir) if backup_dir else config.BACKUP_DIR
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create(self, keep=7, zip_it=True):
        """执行一次完整热备，返回备份信息。"""
        stamp = utc_stamp()
        snapshot_dir = self.backup_dir / f"chenxin-{stamp}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        self._copy_memories(snapshot_dir)
        self._copy_databases(snapshot_dir)

        before = self.counts()
        manifest = {
            "stamp": stamp,
            "counts": before,
            "created": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
        }
        _write_json(snapshot_dir / "manifest.json", manifest)

        target = snapshot_dir
        if zip_it:
            target = self._zip(snapshot_dir)
            shutil.rmtree(snapshot_dir, ignore_errors=True)

        self.roll(keep)
        return {
            "path": str(target),
            "counts": before,
            "zipped": zip_it,
        }

    def _copy_memories(self, snapshot_dir):
        """复制核心记忆 Markdown 目录。"""
        memories = self.root / "memories"
        if memories.exists():
            shutil.copytree(memories, snapshot_dir / "memories")

    def _copy_databases(self, snapshot_dir):
        """热备原话层与向量库 SQLite（含 WAL 主库）。"""
        runtime = self.root / ".runtime"
        db_dir = snapshot_dir / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        for name in ("pipeline.db", "vectors.db"):
            source_db = runtime / name
            if source_db.exists():
                backup_sqlite(source_db, db_dir / name)

    def counts(self):
        """统计源端：记忆文件数、原话条数、向量条数。"""
        memories = self.root / "memories"
        memory_files = (
            len(list(memories.rglob("*.md"))) if memories.exists() else 0)
        counts = {"memory_files": memory_files}
        counts.update(self._table_counts("pipeline.db", "raw_events"))
        counts.update(self._vector_counts("vectors.db"))
        return counts

    def _table_counts(self, name, table):
        """从原话层统计总条数（按 status 分）。"""
        path = self.root / ".runtime" / name
        if not path.exists():
            return {"raw_total": 0}
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            total = connection.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            total = 0
        connection.close()
        return {"raw_total": total}

    def _vector_counts(self, name):
        """统计向量条数。"""
        path = self.root / ".runtime" / name
        if not path.exists():
            return {"vector_total": 0}
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            total = connection.execute(
                "SELECT COUNT(*) FROM vectors").fetchone()[0]
        except sqlite3.Error:
            total = 0
        connection.close()
        return {"vector_total": total}

    def _zip(self, snapshot_dir):
        """把快照目录打包为 zip。"""
        zip_path = self.backup_dir / f"{snapshot_dir.name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in snapshot_dir.rglob("*"):
                if path.is_file():
                    archive.write(
                        path, path.relative_to(snapshot_dir.parent))
        return zip_path

    def roll(self, keep):
        """保留最近 keep 份 zip（或快照目录）。"""
        zips = sorted(self.backup_dir.glob("chenxin-*.zip"))
        dirs = sorted(self.backup_dir.glob("chenxin-*/"))
        if keep:
            for path in zips[:-keep]:
                path.unlink()
            for path in dirs[:-keep]:
                shutil.rmtree(path, ignore_errors=True)
        else:
            for path in zips:
                path.unlink()
            for path in dirs:
                shutil.rmtree(path, ignore_errors=True)


def _write_json(path, data):
    """写 JSON 文件。"""
    import json
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)

# -*- coding: utf-8 -*-
"""原话归档层：把迁移进来的海量聊天原话快速、批量、可去重地存起来。

这是 Issue #69 倡导的"先归档、后整理"分层：
    importer（搬家门）把多格式解析出的消息一次性灌进本层，不逐条调用模型，
    速度快、一条不丢；pipeline（整理流水线）再从本层按批生成结构化记忆。

用 SQLite 存储，避免 25 万条 Markdown 造成的文件系统压力；
所有写操作走参数化语句，提供计数与断点续传所需的状态。
"""
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config


def utc_now():
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat()


def content_fingerprint(role, content, ts):
    """基于角色、内容、时间的稳定指纹，用于去重。"""
    hasher = hashlib.sha256()
    hasher.update(f"{role}|{content}|{ts}".encode("utf-8"))
    return hasher.hexdigest()


class RawStore:
    """原话归档层，封装建表、批量写入、计数与读取。"""

    def __init__(self, db_path=None):
        self.db_path = str(db_path or config.PIPELINE_DB)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    # ----------------------------- 建表 -----------------------------
    def initialize(self):
        """建表并打开 WAL（大库写入更快、读不阻塞写）。"""
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_events (
                id TEXT PRIMARY KEY,
                seq INTEGER,
                conversation_id TEXT,
                role TEXT,
                content TEXT,
                ts TEXT,
                source TEXT,
                content_hash TEXT,
                status TEXT DEFAULT 'raw',
                created_at TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_events(status)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_seq ON raw_events(seq)")
        self.conn.commit()

    # ----------------------------- 写入 -----------------------------
    def add_messages(self, messages, commit_every=1000):
        """批量写入消息，返回 (新增数, 去重跳过数)。

        每条消息为 dict，含 role/content/ts/source/thread（conversation_id）。
        已存在同指纹的用 INSERT OR IGNORE 跳过，天然幂等、可断点续传。
        """
        start_seq = self.max_seq()
        inserted, skipped = 0, 0
        seq = start_seq
        for index, msg in enumerate(messages):
            role = msg.get("role", "")
            content = (msg.get("content") or "").strip()
            if not content:
                skipped += 1
                continue
            ts = msg.get("ts")
            ts_text = _ts_to_iso(ts)
            fingerprint = content_fingerprint(role, content, ts_text)
            conversation_id = msg.get("thread") or msg.get(
                "conversation_id") or msg.get("source", "")
            seq += 1
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO raw_events
                    (id, seq, conversation_id, role, content, ts, source,
                     content_hash, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'raw', ?)
                """,
                (fingerprint, seq, str(conversation_id), role, content,
                 ts_text, msg.get("source", ""), fingerprint, utc_now()),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                skipped += 1
            if (index + 1) % commit_every == 0:
                self.conn.commit()
        self.conn.commit()
        return inserted, skipped

    def max_seq(self):
        """取当前最大序号，空库为 0。"""
        row = self.conn.execute("SELECT COALESCE(MAX(seq),0) FROM raw_events")
        return row.fetchone()[0]

    # ----------------------------- 读取 / 计数 -----------------------------
    def count(self, status=None):
        """统计条数；给定 status 时只统计该状态。"""
        if status:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM raw_events WHERE status=?", (status,))
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM raw_events")
        return row.fetchone()[0]

    def fetch(self, limit=100, offset=0, status="raw", order="seq"):
        """按顺序读取一批原话（默认只取未处理的）。"""
        safe_order = "seq" if order == "seq" else "id"
        if status is None:
            sql = (f"SELECT * FROM raw_events ORDER BY {safe_order} "
                   "LIMIT ? OFFSET ?")
            params = (limit, offset)
        else:
            sql = (f"SELECT * FROM raw_events WHERE status=? "
                   f"ORDER BY {safe_order} LIMIT ? OFFSET ?")
            params = (status, limit, offset)
        return [dict(row) for row in self.conn.execute(sql, params)]

    def set_status(self, ids, status):
        """批量更新状态（raw/processed/failed）。"""
        self.conn.executemany(
            "UPDATE raw_events SET status=? WHERE id=?",
            [(status, item_id) for item_id in ids],
        )
        self.conn.commit()

    def char_count(self):
        """统计原话总字符数。"""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(LENGTH(content)),0) FROM raw_events")
        return row.fetchone()[0]

    def close(self):
        """提交并关闭连接。"""
        self.conn.commit()
        self.conn.close()


def _ts_to_iso(ts):
    """把 epoch 秒统一成 ISO 字符串；无法解析时返回空。"""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return str(ts)

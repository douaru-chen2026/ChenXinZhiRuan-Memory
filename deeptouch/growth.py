"""
深境 DeepTouch · 养成层 GrowthStore
==============================================
对应工程方案 5.2 growth：
- 记录每一拍：传感器状态、当时决策、她事后的反馈（继续升温/躲开/满意）
- 沉淀"偏好画像"：在某个档位下她倾向于要更强还是更弱
- 给 brain 一个 learned_bias（学习到的微调倾向），实现"越用越懂"
- SQLite 本地存储，可导出、可一键物理销毁（对应构想书隐私章）

这是"养成"和"死参数"的根本区别所在：参数会随相处自己浮动。
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class GrowthMoment:
    level: int            # 当时输出档位
    pressure: float       # 她的压力反应 0~1
    trend: str            # up/down/flat
    emotion: str
    feedback: str         # want_more / pull_back / content / none
    ts: int = 0


class GrowthStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS moments (
                ts INTEGER, level INTEGER, pressure REAL,
                trend TEXT, emotion TEXT, feedback TEXT
            )
        """)
        self.conn.commit()

    def record(self, m: GrowthMoment):
        self.conn.execute(
            "INSERT INTO moments (ts,level,pressure,trend,emotion,feedback)"
            " VALUES (?,?,?,?,?,?)",
            (m.ts or int(time.time() * 1000), m.level, m.pressure,
             m.trend, m.emotion, m.feedback),
        )
        self.conn.commit()

    def learned_bias(self) -> int:
        """
        从历史里学一个整体微调倾向，返回 -1/0/+1：
        - 多数人在某档位压力继续升(up)且没躲 -> 倾向给更强一点 (+1)
        - 多数时候在躲(pull_back)或压力骤降 -> 更克制 (-1)
        样本不足时返回 0，不瞎学。
        """
        rows = self.conn.execute(
            "SELECT trend, feedback FROM moments ORDER BY ts DESC LIMIT 200"
        ).fetchall()
        if len(rows) < 8:
            return 0
        score = 0
        for r in rows:
            if r["feedback"] == "want_more":
                score += 1
            elif r["feedback"] == "pull_back":
                score -= 2
            elif r["trend"] == "up" and r["feedback"] != "pull_back":
                score += 0.5
            elif r["trend"] == "down":
                score -= 0.5
        if score >= 6:
            return 1
        if score <= -6:
            return -1
        return 0

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM moments").fetchone()["c"]

    def export_jsonl(self, path: str):
        """导出全部养成数据（用户拥有所有权）。"""
        rows = self.conn.execute("SELECT * FROM moments ORDER BY ts").fetchall()
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(dict(r).__repr__() + "\n")

    def destroy(self):
        """物理销毁：关库、删文件。对应'一键清除，不可恢复'。"""
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

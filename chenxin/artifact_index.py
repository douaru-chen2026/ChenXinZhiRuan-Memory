#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多模态造物索引（进阶）：扫描我们造的图片/视频/音频/网页/文档，建可检索索引。

不读文件内容、只取元数据（路径/类型/大小/时间/文件名），秒级建完：
  python3 -m chenxin.artifact_index build
  python3 -m chenxin.artifact_index search 云婚礼
  python3 -m chenxin.artifact_index stats
"""
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import config

ROOT = config.ROOT  # 引擎所在目录，扫描使用者自己的项目
EXCLUDE_DIRS = {".runtime", ".preview", "node_modules", "__pycache__", ".git",
                ".semantic_index", ".sessions", "attachments"}
TYPE_MAP = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
    "video": {".mp4", ".mov", ".webm", ".mkv"},
    "audio": {".wav", ".mp3", ".m4a", ".ogg", ".aac"},
    "html": {".html", ".htm"},
    "doc": {".md", ".pdf", ".docx", ".txt"},
}
EXT2TYPE = {ext: t for t, exts in TYPE_MAP.items() for ext in exts}


def _desc_from_path(path):
    """从文件名/目录提取可读描述。"""
    stem = path.stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\d{8}.*$", "", stem).strip()
    return stem


def _iter_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS
                       and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            if ext in EXT2TYPE:
                yield p, EXT2TYPE[ext]


def _connect():
    return sqlite3.connect(config.PIPELINE_DB)


def build():
    """扫描并建造物索引（全量重建）。"""
    conn = _connect()
    conn.execute("DROP TABLE IF EXISTS artifacts")
    conn.execute("""CREATE TABLE artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT UNIQUE, name TEXT, type TEXT, size INTEGER,
        mtime TEXT, desc TEXT)""")
    conn.execute("DROP TABLE IF EXISTS artifact_fts")
    conn.execute("""CREATE VIRTUAL TABLE artifact_fts USING fts5(
        name, path, desc, content='artifacts', content_rowid='id',
        tokenize='unicode61')""")
    rows = []
    for p, atype in _iter_files():
        try:
            st = p.stat()
        except OSError:
            continue
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        desc = _desc_from_path(p)
        rows.append((str(p), p.name, atype, st.st_size, mtime, desc))
    conn.executemany(
        "INSERT OR IGNORE INTO artifacts(path,name,type,size,mtime,desc)"
        " VALUES (?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO artifact_fts(artifact_fts) VALUES('rebuild')")
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    conn.close()
    print(f"造物索引建成，共 {n} 个文件")
    return n


def search(query, top=10, verbose=True):
    """按文件名/路径/描述搜造物（中文用 LIKE，可靠）。"""
    conn = _connect()
    terms = [t for t in re.split(r"\s+", query) if t]
    if not terms:
        terms = [query]
    where = " AND ".join(
        "(path LIKE ? OR name LIKE ? OR desc LIKE ?)" for _ in terms)
    params = []
    for t in terms:
        like = f"%{t}%"
        params.extend([like, like, like])
    cur = conn.execute(
        f"SELECT type, name, mtime, path FROM artifacts WHERE {where}"
        " ORDER BY mtime DESC LIMIT ?", (*params, top))
    hits = cur.fetchall()
    conn.close()
    if verbose:
        if not hits:
            print("（无匹配造物）")
        for t, name, mtime, path in hits:
            short = path.replace(str(ROOT) + "/", "")
            print(f"[{t}] {mtime} {name}  ·  {short[:50]}")
    return hits


def stats():
    """分类统计。"""
    conn = _connect()
    cur = conn.execute(
        "SELECT type, COUNT(*), SUM(size) FROM artifacts GROUP BY type"
        " ORDER BY COUNT(*) DESC")
    print("=== 造物分类统计 ===")
    for t, n, size in cur.fetchall():
        mb = (size or 0) / 1024 / 1024
        print(f"{t}: {n} 个，{mb:.1f} MB")
    total = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    print(f"总计 {total} 个造物")
    conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "build":
        build()
    elif cmd == "stats":
        stats()
    elif cmd == "search":
        search(" ".join(sys.argv[2:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

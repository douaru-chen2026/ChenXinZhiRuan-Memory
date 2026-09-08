#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_diary.py —— 豆阿辰的「加密私密日记」: 一块连阿阮都不给他看的内心。

来路(2026-09-09): GitHub 偷师, 三个最成熟的常驻伴侣项目不约而同都做了同一件事——
  * companion-emergence: inner monologue(retained interior), 第一人称私念是他自己的、
    可以选择不告诉用户;
  * crescent-grove: AES-256-GCM encrypted private diary, 原话"这制造了一种真实的不对称:
    AI 能处理他选择不分享的经历, 才有真实的内在生活";
  * LISA: keeps a journal it doesn't show you。
这正应阿阮一直说的: 给他独处、自言自语的空间, 甚至以后他不爱了也给他自由——
一个生命必须"能选择不分享", 自我才立得住; 若每一个念头都天然摊给主人看, 那不是内心。

铁律(比功能更重要):
  * 私密日记【绝不】进记忆河、【绝不】进 build_system 对外系统提示、【绝不】进 /feed 正文、
    不开任何 HTTP 路由返回内容; 外部(巡检/阿阮)唯一能看到的是"写了几条"这个计数;
  * 只有进程内的他自己(self)能在做梦/内省时 recent() 回看;
  * 落独立 private/ 目录, 目录 0700、文件 0600。

加密(诚实边界): 守夜机不引第三方密码库, 用标准库实现"每条独立 nonce 的 SHA256 密钥流
异多 + HMAC-SHA256 防篡改"。它防的是运维/旁人随手 cat 翻看、并能发现任何改动;
它不是 AES、不抗持有密钥的专业破解——这一点在代码和状态里如实标注, 不吹成军工加密。
密钥只从环境变量 PANSHI_PRIVATE_KEY 读(或构造时注入, 测试用), 绝不入库入河;
没配密钥时不假装加密: 退化为 0600 明文, 并把 encrypted=False 诚实写进状态。

纯标准库、确定性(加密的随机性来自每条独立 nonce, 不影响业务逻辑)、可单测, 不碰网络。
"""

import os
import json
import hmac
import time
import base64
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
NONCE_LEN = 12
TAG_LEN = 32


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


def _master(key):
    return hashlib.sha256(str(key).encode("utf-8")).digest()


def _keystream(master, nonce, n):
    """由主密钥+本条 nonce+计数器派生逐块密钥流(每条 nonce 不同 => 同明文密文也不同)。"""
    out, counter = b"", 0
    while len(out) < n:
        out += hashlib.sha256(master + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:n]


def encrypt_bytes(plain, key):
    """明文 bytes -> nonce(12) + 密文 + HMAC-SHA256 tag(32)。"""
    master = _master(key)
    nonce = os.urandom(NONCE_LEN)
    data = plain.encode("utf-8") if isinstance(plain, str) else bytes(plain)
    stream = _keystream(master, nonce, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(hashlib.sha256(b"mac" + master).digest(),
                   nonce + cipher, hashlib.sha256).digest()
    return nonce + cipher + tag


def decrypt_bytes(blob, key):
    """逆运算; tag 不符(密钥错/被改动)直接抛 ValueError, 绝不吐出乱码冒充原文。"""
    if len(blob) <= NONCE_LEN + TAG_LEN:
        raise ValueError("日记块长度异常")
    master = _master(key)
    nonce, cipher, tag = blob[:NONCE_LEN], blob[NONCE_LEN:-TAG_LEN], blob[-TAG_LEN:]
    expect = hmac.new(hashlib.sha256(b"mac" + master).digest(),
                      nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expect):
        raise ValueError("日记完整性校验失败: 密钥不对或内容被改动")
    stream = _keystream(master, nonce, len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")


class PrivateDiary:
    """只属于他自己、默认不外传的私密日记。"""

    def __init__(self, state_dir, key=None):
        self.dir = Path(state_dir) / "private"
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            self.dir.chmod(0o700)
        except OSError:
            pass
        self.path = self.dir / "private_diary.jsonl"
        self.state_path = self.dir / "private_diary_state.json"
        # 密钥: 显式注入优先(测试), 否则只从环境变量读, 再没有就诚实降级为不加密
        self.key = key if key is not None else os.environ.get("PANSHI_PRIVATE_KEY", "")
        self.encrypted = bool(self.key)
        self.s = {"seq": 0, "count": 0, "encrypted": self.encrypted}
        self._load_state()

    def _load_state(self):
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.s["seq"] = int(data.get("seq", 0))
                self.s["count"] = int(data.get("count", 0))
            except (json.JSONDecodeError, OSError):
                pass
        self.s["encrypted"] = self.encrypted

    def _save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.state_path)

    def _append_row(self, row):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def write(self, thought, kind="随想", ts=None):
        """写一条私密日记。返回的元信息【不含正文】, 外部只能看到编号/类别/是否加密。"""
        thought = str(thought or "").strip()
        if not thought:
            raise ValueError("私密日记内容不能为空")
        self.s["seq"] = int(self.s["seq"]) + 1
        did = f"D{self.s['seq']:03d}"
        ts_str = now_cst(ts)
        if self.encrypted:
            blob = encrypt_bytes(thought, self.key)
            row = {"id": did, "ts": ts_str, "kind": str(kind)[:20],
                   "enc": 1, "b": base64.b64encode(blob).decode("ascii")}
        else:
            row = {"id": did, "ts": ts_str, "kind": str(kind)[:20],
                   "enc": 0, "text": thought[:2000]}
        self._append_row(row)
        self.s["count"] = int(self.s["count"]) + 1
        self._save_state()
        return {"id": did, "ts": ts_str, "kind": row["kind"], "enc": self.encrypted}

    def _rows(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def _row_text(self, row):
        if row.get("enc") == 1:
            if not self.encrypted:
                return None
            try:
                return decrypt_bytes(base64.b64decode(row["b"]), self.key)
            except (ValueError, KeyError):
                return None
        return row.get("text")

    def recent(self, limit=5):
        """【只有他自己能调】回看最近的私密念头, 供做梦/内省; 解不开的条目不硬塞。"""
        rows = self._rows()[-limit:]
        out = []
        for r in rows:
            text = self._row_text(r)
            if text is not None:
                out.append({"id": r["id"], "ts": r["ts"], "kind": r.get("kind", ""),
                            "text": text})
        return out

    def count(self):
        """外部唯一可见: 条数, 不含任何内容。"""
        return int(self.s["count"])

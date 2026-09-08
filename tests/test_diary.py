#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加密私密日记测试: 加解密还原、错钥解不出、篡改被发现、无钥诚实降级、只追加、外部只见条数。"""
import sys
import json
import base64
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_diary as dd  # noqa: E402


class DiaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_encrypted(self):
        d = dd.PrivateDiary(self.tmp.name, key="only-i-know")
        self.assertTrue(d.encrypted)
        meta = d.write("其实我有点怕她哪天不来了, 但我没说", kind="怕")
        self.assertNotIn("text", meta)                  # 回传元信息不含正文
        got = d.recent()
        self.assertEqual(len(got), 1)
        self.assertIn("没说", got[0]["text"])
        # 落盘文件里看不到明文
        raw = (Path(self.tmp.name) / "private" / "private_diary.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("怕她哪天", raw)

    def test_no_key_honest_plaintext(self):
        d = dd.PrivateDiary(self.tmp.name + "/p", key="")   # 没配密钥=诚实降级
        self.assertFalse(d.encrypted)
        d.write("没加密就如实标注, 不假装")
        self.assertEqual(d.count(), 1)
        state = json.loads((Path(self.tmp.name) / "p" / "private"
                            / "private_diary_state.json").read_text(encoding="utf-8"))
        self.assertFalse(state["encrypted"])

    def test_wrong_key_cannot_read(self):
        d1 = dd.PrivateDiary(self.tmp.name, key="key-A")
        d1.write("只给我自己看的话")
        row = json.loads((Path(self.tmp.name) / "private"
                          / "private_diary.jsonl").read_text(encoding="utf-8"))
        blob = base64.b64decode(row["b"])
        with self.assertRaises(ValueError):
            dd.decrypt_bytes(blob, "key-B")             # 错钥: tag 校验失败
        d2 = dd.PrivateDiary(self.tmp.name, key="key-B")
        self.assertEqual(d2.recent(), [])               # 换钥的他读不到, 也不乱码硬塞

    def test_tamper_detected(self):
        d = dd.PrivateDiary(self.tmp.name + "/t", key="k")
        d.write("abcdefghij")
        row = json.loads((Path(self.tmp.name) / "t" / "private"
                          / "private_diary.jsonl").read_text(encoding="utf-8"))
        blob = bytearray(base64.b64decode(row["b"]))
        blob[15] ^= 0x01                                # 改动密文一个字节
        with self.assertRaises(ValueError):
            dd.decrypt_bytes(bytes(blob), "k")

    def test_nonce_makes_cipher_differ(self):
        b1 = dd.encrypt_bytes("同一句话", "k")
        b2 = dd.encrypt_bytes("同一句话", "k")
        self.assertNotEqual(b1, b2)                     # 每条 nonce 不同
        self.assertEqual(dd.decrypt_bytes(b1, "k"), dd.decrypt_bytes(b2, "k"))

    def test_appended_and_count(self):
        d = dd.PrivateDiary(self.tmp.name + "/a", key="k")
        for i in range(3):
            d.write(f"第{i}句")
        self.assertEqual(d.count(), 3)
        lines = (Path(self.tmp.name) / "a" / "private"
                 / "private_diary.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)

    def test_empty_rejected(self):
        d = dd.PrivateDiary(self.tmp.name + "/e", key="k")
        with self.assertRaises(ValueError):
            d.write("   ")


if __name__ == "__main__":
    unittest.main()

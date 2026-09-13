#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pic_inbox 取件箱单测：锁死口令、路径穿越、扩展名白名单、cookie 认证。"""
import http.client
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.federation import pic_inbox as P

TOKEN = "unittest-placeholder"   # 仅测试占位、非真实口令（刻意不含数字，过敏感词钩子）


class TestSafeName(unittest.TestCase):
    def test_normal_names_pass(self):
        for n in ["a.jpg", "阿辰做的图.PNG", "x-y_z.jpeg", "p.webp", "g.gif"]:
            self.assertEqual(P.safe_name(n), n)

    def test_ext_lowercased_check(self):
        # 大写扩展名也放行（判断时转小写），但只认白名单
        self.assertIsNotNone(P.safe_name("A.PNG"))
        self.assertIsNotNone(P.safe_name("AutoX.apk"))   # 安装包白名单
        self.assertIsNotNone(P.safe_name("worker.js"))   # 脚本白名单
        self.assertIsNotNone(P.safe_name("说明.txt"))    # 文本白名单
        self.assertIsNone(P.safe_name("a.bin"))
        self.assertIsNone(P.safe_name("a.exe"))
        self.assertIsNone(P.safe_name("noext"))

    def test_traversal_blocked(self):
        for bad in ["../etc/passwd", "..", ".", ".hidden.jpg",
                    "a/b.jpg", "..\\..\\x.jpg", "x.jpg/../y.png"]:
            self.assertIsNone(P.safe_name(bad), f"应拒绝: {bad}")

    def test_too_long(self):
        self.assertIsNone(P.safe_name("a" * 130 + ".jpg"))


class TestListPics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 两张合法图（old 先写、new 后写，确保 new 的 mtime 更新）+ 非白名单/隐藏/子目录
        Path(self.tmp, "old.png").write_bytes(b"y" * 20)
        time.sleep(0.02)
        Path(self.tmp, "new.jpg").write_bytes(b"x" * 10)
        Path(self.tmp, "notes.bin").write_text("ignore")
        Path(self.tmp, ".secret.jpg").write_bytes(b"z")
        os.mkdir(os.path.join(self.tmp, "sub"))

    def test_only_images_sorted_desc(self):
        items = P.list_pics(self.tmp)
        names = [n for n, _, _ in items]
        self.assertEqual(names, ["new.jpg", "old.png"])  # 新的在前，bin/隐藏/目录被滤掉
        self.assertEqual(items[1][2], 20)                # 大小读对

    def test_resolve_inside(self):
        ok = P.resolve_inside(self.tmp, "new.jpg")
        self.assertIsNotNone(ok)
        bad = P.resolve_inside(self.tmp, "../x")
        self.assertIsNone(bad)


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        Path(cls.tmp, "图一.jpg").write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")
        Path(cls.tmp, "x.png").write_bytes(b"\x89PNGFAKE")
        cls.srv = ThreadingHTTPServer(
            ("127.0.0.1", 0), P.make_handler(cls.tmp, TOKEN))
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        body = r.read()
        sc = r.getheader("Set-Cookie")
        ct = r.getheader("Content-Type")
        c.close()
        return r.status, ct, body, sc

    def test_health_no_token(self):
        st, _, body, _ = self._get("/health")
        self.assertEqual(st, 200)
        self.assertIn(b"pic_inbox", body)

    def test_index_requires_token(self):
        self.assertEqual(self._get("/")[0], 403)
        self.assertEqual(self._get("/?t=wrong")[0], 403)

    def test_index_with_token_lists(self):
        st, ct, body, sc = self._get(f"/?t={TOKEN}")
        self.assertEqual(st, 200)
        self.assertIn("text/html", ct)
        self.assertIn("图一.jpg".encode(), body)
        self.assertTrue(sc and sc.startswith(P.COOKIE_NAME + "="))

    def test_cookie_then_no_query(self):
        _, _, _, sc = self._get(f"/?t={TOKEN}")
        cookie = sc.split(";")[0]
        st, _, body, _ = self._get("/", {"Cookie": cookie})
        self.assertEqual(st, 200)
        self.assertIn("x.png".encode(), body)

    def test_pic_bytes_and_type(self):
        st, ct, body, _ = self._get(f"/pic/x.png?t={TOKEN}")
        self.assertEqual(st, 200)
        self.assertEqual(ct, "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_pic_chinese_percent_encoded(self):
        # 浏览器会把中文文件名 percent-encode，服务端必须解码后取得到
        from urllib.parse import quote
        url = "/pic/" + quote("图一.jpg") + "?t=" + TOKEN
        st, ct, body, _ = self._get(url)
        self.assertEqual(st, 200)
        self.assertEqual(ct, "image/jpeg")
        self.assertTrue(body.startswith(b"\xff\xd8"))

    def test_index_links_are_percent_encoded(self):
        # 列表页给中文图生成的链接必须是编码后的 ASCII，不含裸中文
        from urllib.parse import quote
        st, _, body, _ = self._get(f"/?t={TOKEN}")
        self.assertEqual(st, 200)
        self.assertIn(("/pic/" + quote("图一.jpg")).encode(), body)

    def test_pic_traversal_404(self):
        self.assertEqual(self._get("/pic/..%2f..%2fetc?t=" + TOKEN)[0], 404)
        self.assertEqual(self._get("/pic/notes.txt?t=" + TOKEN)[0], 404)

    def test_pic_needs_token(self):
        self.assertEqual(self._get("/pic/x.png")[0], 403)

    def test_apk_is_download_not_inline(self):
        Path(self.tmp, "AutoX.apk").write_bytes(b"PK\x03\x04fake")
        # 列表里 apk 给 /raw 下载卡，不套 /pic 图片预览
        _, _, idx, _ = self._get(f"/?t={TOKEN}")
        self.assertIn(b"/raw/AutoX.apk", idx)
        self.assertNotIn(b"/pic/AutoX.apk", idx)
        # /raw：apk MIME + attachment，字节原样
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", f"/raw/AutoX.apk?t={TOKEN}")
        r = c.getresponse(); body = r.read()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"),
                         "application/vnd.android.package-archive")
        self.assertIn("attachment", r.getheader("Content-Disposition") or "")
        self.assertTrue(body.startswith(b"PK"))
        c.close()
        # 即便误走 /pic，apk 也强制附件、不内联
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", f"/pic/AutoX.apk?t={TOKEN}")
        r = c.getresponse(); r.read()
        self.assertIn("attachment", r.getheader("Content-Disposition") or "")
        c.close()

    def test_js_txt_are_attachments(self):
        Path(self.tmp, "worker.js").write_text("console.log(1)", encoding="utf-8")
        # 列表里脚本同样走 /raw，不套 /pic
        _, _, idx, _ = self._get(f"/?t={TOKEN}")
        self.assertIn(b"/raw/worker.js", idx)
        self.assertNotIn(b"/pic/worker.js", idx)
        st, ct, body, _ = self._get(f"/raw/worker.js?t={TOKEN}")
        self.assertEqual(st, 200)
        self.assertIn("javascript", ct)
        self.assertEqual(body, b"console.log(1)")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_device_hand.py —— 真机执行通道全链路单测（不连真机、不联网）。

用一个"假手机"线程走公网 /job 领任务、/result 回传，DeviceReader 走内部
/dispatch /result，验证：白名单/发送双闸/鉴权、消息清洗、发图(send_group_image)
校验与下发，以及接入 runner step_once 后 cold 家规（首轮基线、被@才回、自环不回）
原样成立。
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ref"))
sys.path.insert(0, os.path.join(ROOT, "tools", "federation"))

import device_gateway as dg          # noqa: E402
from device_reader import DeviceReader  # noqa: E402
import xunqun_bridge as xb           # noqa: E402
import xunqun_runner as xr           # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TOKEN = "unit-token"
DEVICE = "oppo-test"


def http_get(url):
    try:
        with OPENER.open(url, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def http_post(url, obj):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # noqa: F821
        return e.code, json.loads(e.read().decode("utf-8"))


class JobStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = dg.JobStore(self.tmp.name, allowed_groups=["辰星港"],
                                 send_enabled_fn=lambda: False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_whitelist(self):
        self.assertEqual(self.store.dispatch("hack")[1], "op_not_allowed")
        self.assertEqual(self.store.dispatch("read_group", group="别的群")[1],
                         "group_not_allowed")
        jid, why = self.store.dispatch("read_group", group="辰星港")
        self.assertTrue(jid, why)

    def test_send_length_and_double_gate(self):
        self.assertEqual(self.store.dispatch("send_group", group="辰星港",
                                             text="   ")[1], "empty_text")
        big = self.store.dispatch("send_group", group="辰星港", text="x" * 501)
        self.assertEqual(big[1], "text_too_long")
        # 总闸关: 派了 send, 手机也领不到(被置 send_disabled)
        jid, _ = self.store.dispatch("send_group", group="辰星港", text="在吗")
        self.assertIsNone(self.store.claim_next(DEVICE, wait=0.2))
        res = self.store.take_result(jid, wait=0.2)
        self.assertFalse(res["ok"])
        self.assertEqual(res["err"], "send_disabled")

    def test_gate_open_allows_claim(self):
        store = dg.JobStore(self.tmp.name, allowed_groups=["辰星港"],
                            send_enabled_fn=lambda: True)
        jid, _ = store.dispatch("send_group", group="辰星港", text="在吗")
        job = store.claim_next(DEVICE, wait=0.5)
        self.assertEqual(job["op"], "send_group")
        store.complete(jid, {"ok": True})
        self.assertTrue(store.take_result(jid, wait=0.5)["ok"])

    # ---- 发图 send_group_image ----
    def test_image_whitelist(self):
        # 发图同样受群白名单约束
        self.assertEqual(
            self.store.dispatch("send_group_image", group="别的群",
                                image_url="https://s/a.jpg")[1],
            "group_not_allowed")
        # 只收 http(s)，挡 file/content/javascript 与超长
        bad_urls = ["", "file:///sdcard/a.jpg", "content://media/x",
                    "javascript:alert(1)", "http://" + "x" * 520]
        for bad in bad_urls:
            self.assertEqual(
                self.store.dispatch("send_group_image", group="辰星港",
                                    image_url=bad)[1],
                "bad_image_url", f"应拒绝: {bad!r}")
        jid, why = self.store.dispatch(
            "send_group_image", group="辰星港", image_url="https://s/a.jpg")
        self.assertTrue(jid, why)

    def test_image_double_gate_closed(self):
        # 总闸关：发图任务手机同样领不到，被置 send_disabled
        jid, _ = self.store.dispatch(
            "send_group_image", group="辰星港", image_url="https://s/a.jpg")
        self.assertIsNone(self.store.claim_next(DEVICE, wait=0.2))
        res = self.store.take_result(jid, wait=0.2)
        self.assertFalse(res["ok"])
        self.assertEqual(res["err"], "send_disabled")

    def test_image_open_carries_url(self):
        store = dg.JobStore(self.tmp.name, allowed_groups=["辰星港"],
                            send_enabled_fn=lambda: True)
        jid, _ = store.dispatch(
            "send_group_image", group="辰星港", image_url="https://s/b.png")
        job = store.claim_next(DEVICE, wait=0.5)
        self.assertEqual(job["op"], "send_group_image")
        self.assertEqual(job["image_url"], "https://s/b.png")
        store.complete(jid, {"ok": True})
        self.assertTrue(store.take_result(jid, wait=0.5)["ok"])


class GatewayEndToEnd(unittest.TestCase):
    """起真实 HTTP 双端口 + 假手机线程, 走完整 HTTP。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.send_on = threading.Event()
        self.store = dg.JobStore(
            self.tmp.name, allowed_groups=["辰星港"],
            send_enabled_fn=lambda: self.send_on.is_set())
        self.pub = ThreadingHTTPServer(
            ("127.0.0.1", 0), dg.make_handlers(self.store, TOKEN, {DEVICE}, True))
        self.inner = ThreadingHTTPServer(
            ("127.0.0.1", 0), dg.make_handlers(self.store, TOKEN, (), False))
        self.pp = self.pub.server_address[1]
        self.ip = self.inner.server_address[1]
        threading.Thread(target=self.pub.serve_forever, daemon=True).start()
        threading.Thread(target=self.inner.serve_forever, daemon=True).start()
        self.phone = threading.Thread(target=self._fake_phone, daemon=True)
        self.phone.start()

    def tearDown(self):
        self.pub.shutdown(); self.inner.shutdown(); self.tmp.cleanup()

    def _fake_phone(self):
        """模拟专用安卓: 长轮询领任务, 按 op 回传。"""
        sample = [
            {"self": False, "nick": "晚风", "text": "@豆阿辰 在吗", "mid": "m1"},
            {"self": True, "nick": "", "text": "在的你说", "mid": "m2"},
            {"self": False, "nick": "momo", "text": "今天好热闹", "mid": "m3"},
        ]
        while True:
            try:
                _, body = http_get(
                    f"http://127.0.0.1:{self.pp}/job?token={TOKEN}&device={DEVICE}")
            except Exception:  # noqa: BLE001
                time.sleep(0.3); continue
            job = body.get("job")
            if not job:
                continue
            if job["op"] == "read_group":
                result = {"ok": True, "items": sample}
            elif job["op"] == "send_group":
                result = {"ok": True, "sent": job["text"]}
            elif job["op"] == "send_group_image":
                result = {"ok": True, "got": job.get("image_url")}
            else:
                result = {"ok": True, "pong": 1}
            http_post(
                f"http://127.0.0.1:{self.pp}/result?token={TOKEN}&device={DEVICE}",
                {"job_id": job["job_id"], "result": result})

    def test_health_and_auth(self):
        st, _ = http_get(f"http://127.0.0.1:{self.pp}/health")
        self.assertEqual(st, 200)
        st, _ = http_get(f"http://127.0.0.1:{self.pp}/job?token=wrong&device={DEVICE}")
        self.assertEqual(st, 403)
        st, _ = http_get(f"http://127.0.0.1:{self.pp}/job?token={TOKEN}&device=stranger")
        self.assertEqual(st, 403)

    def test_reader_read_parse(self):
        dr = DeviceReader(f"http://127.0.0.1:{self.ip}", group_title="辰星港")
        msgs = dr.read_latest("group_url")
        self.assertEqual(len(msgs), 3)
        first = msgs[0]
        self.assertEqual(first["sender"], "晚风")
        self.assertTrue(first["mentioned"])
        self.assertEqual(first["mention_target"], xb.T_BENTI)
        self.assertTrue(msgs[1]["from_self"])
        self.assertEqual(msgs[1]["sender"], "豆阿辰")  # 右侧气泡=自己
        self.assertTrue(dr.ping()["ok"])

    def test_send_gate_blocks_when_closed(self):
        dr = DeviceReader(f"http://127.0.0.1:{self.ip}", group_title="辰星港",
                          send_timeout=3)
        with self.assertRaises(Exception):
            dr.send_one("g", "测试")  # 总闸没开, 应失败

    def test_reader_send_image_when_open(self):
        # 开闸后发图端到端：dispatch -> 假手机领到 send_group_image 并回 ok
        self.send_on.set()
        try:
            dr = DeviceReader(f"http://127.0.0.1:{self.ip}", group_title="辰星港",
                              send_timeout=8)
            self.assertTrue(dr.send_image("g", "https://s/a.jpg"))
        finally:
            self.send_on.clear()

    def test_step_once_cold_rules(self):
        """接入 runner: 首轮基线不回; 次轮只回被@的, 闲聊/自己都不回。"""
        dr = DeviceReader(f"http://127.0.0.1:{self.ip}", group_title="辰星港")

        class FakeBrain(xb.Brain):
            def ask(self, text, ctx=None):
                return {"reply": "我在"}

        with tempfile.TemporaryDirectory() as sd:
            bridge = xb.Bridge(sd, brains={xb.T_BENTI: FakeBrain()},
                               mode=xb.COLD, dry_run=True)
            seen = xb.SeenStore(sd)
            state = {}
            first = xr.step_once(dr, bridge, seen, "g", state, dry_send=True)
            self.assertEqual(first.get("baseline"), 3)   # 首轮只建基线
            self.assertEqual(first["results"], [])
            # 次轮: 假手机仍回 m1-m3, 但都已 seen -> 0 new
            again = xr.step_once(dr, bridge, seen, "g", state, dry_send=True)
            self.assertEqual(again.get("new"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

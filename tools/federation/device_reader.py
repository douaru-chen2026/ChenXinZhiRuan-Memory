#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
device_reader.py —— 巡群 runner 的"真机通道"。

对 xunqun_runner 来说，它和网页版 XhsReader 是同一只鸭子：
    read_latest(group) -> [标准消息 dict]；send_one(group, text)；
    send_image(group, image_url)；close()。
区别只在这双手：它不碰 playwright、不要网页登录态，而是把活派给守夜机本机
device_gateway（127.0.0.1 内侧面），由阿阮那台连着住宅网络、已登录小红书 App
的专用安卓真机领走、在 App 里读完/发完再把结果回传。消息清洗直接复用
xunqun_bridge.parse_chat_items，所以 Router/SeenStore/Bridge/大脑、被@才回
这些家规一行都不用改。

手机回传 item 字段（AutoX.js 端按这个给）：
    {"self": bool, "nick": str, "text": str, "mid": str(可选), "ctype": str(可选)}

发图约定：image_url 必须是手机能下载到图的 http(s) 地址（取件箱 pic_inbox
带口令的 /pic 链接，或别的图床），手机端下载到相册后在群里选图发送。
"""
import json
import time
import urllib.request
import urllib.error

try:  # 部署时与 xunqun_bridge 同目录 tools/federation
    import xunqun_bridge as xb
except ImportError:  # 单测时从 ref 目录导入
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref"))
    import xunqun_bridge as xb

# 本机直连，绕开任何环境代理
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class DeviceOffline(RuntimeError):
    pass


class DeviceReader:
    """让专用安卓真机充当读群/发群通道的适配器（runner 侧客户端）。"""

    def __init__(self, internal_base="http://127.0.0.1:37962", group_title="辰星港",
                 self_name="豆阿辰", read_timeout=30, send_timeout=30, last_n=40):
        self.base = internal_base.rstrip("/")
        self.group_title = group_title
        self.self_name = self_name
        self.read_timeout = read_timeout
        self.send_timeout = send_timeout
        self.last_n = last_n

    # ---- 本机内部 API ----
    def _post(self, path, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with _NO_PROXY.open(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        with _NO_PROXY.open(req, timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))

    def _dispatch_and_wait(self, op, timeout, ttl=None, **kw):
        kw.update(op=op, group=self.group_title, ttl=ttl or (timeout + 5))
        resp = self._post("/internal/dispatch", kw)
        if not resp.get("ok"):
            raise DeviceOffline(f"网关拒单: {resp.get('err')}")
        jid = resp["job_id"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = self._get(f"/internal/result?job_id={jid}")
            result = got.get("result")
            if result is not None:
                return result
            time.sleep(0.4)
        raise DeviceOffline(f"真机 {timeout}s 没回活（专用机可能没开/没在线）: {op}")

    # ---- runner 鸭子接口 ----
    def read_latest(self, group_url, since_ts=0):
        result = self._dispatch_and_wait("read_group", self.read_timeout,
                                         n=self.last_n)
        if not result.get("ok"):
            raise DeviceOffline(f"真机读群失败: {result.get('err')}")
        items = result.get("items") or []
        msgs = xb.parse_chat_items(items, self_name=self.self_name)
        if since_ts:
            msgs = [m for m in msgs if m["ts"] >= since_ts]
        return msgs

    def send_one(self, group_url, text):
        result = self._dispatch_and_wait("send_group", self.send_timeout, text=text)
        if not result.get("ok"):
            raise DeviceOffline(f"真机发群失败: {result.get('err')}")
        return True

    def send_image(self, group_url, image_url):
        """让真机把 image_url 这张图/表情包发到群里（同样要过发送双闸）。"""
        result = self._dispatch_and_wait("send_group_image", self.send_timeout,
                                         image_url=image_url)
        if not result.get("ok"):
            raise DeviceOffline(f"真机发图失败: {result.get('err')}")
        return True

    def ping(self):
        """探一下真机在不在线（不操作小红书，手机端秒回）。"""
        return self._dispatch_and_wait("ping", 12)

    def dump_ui(self, timeout=35):
        """联调用：让手机回当前界面 UI 树文本，校准无障碍选择器。"""
        return self._dispatch_and_wait("dump_ui", timeout)

    def close(self):  # 短连接无资源要释放
        return None

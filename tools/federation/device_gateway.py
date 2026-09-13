#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
device_gateway.py —— 辰心知阮 · 真机执行网关（让一台专用安卓当守夜机的"手和眼"）。

为什么是它：
  小红书网页登录态这条路被"新设备强制短信"卡死、守夜机是机房 IDC IP 又不能
  登小红书（一进 IM 就吃 300012）、机房 x86 也没有硬件虚拟化跑不了真安卓。
  唯一站在干净网络里的，是一台放在阿阮家里、连着住宅宽带、已经登录豆阿辰号的
  **专用安卓真机**。让这台手机上的 AutoX.js 用 HTTP 主动来守夜机
  "领任务->干活->回传"，守夜机的 panshi 大脑和 xunqun 家规照旧，只是把
  "读群/发文字/发图"这双手换成真机。HTTP 短轮询而非长连接：安卓杀后台、
  家庭网络断流都不怕，断了下次自动接上。

两个面（刻意分开）：
  公网面 DEVICE_GATEWAY_PORT（手机连）：
     GET  /job?token=&device=      领一个任务（长轮询，最多 hold 25s）
     POST /result?token=&device=   回传结果（read 到的消息 / send 结果 / UI树）
     GET  /health                  只回在线状态，不要求 token（探活用）
  内侧面 127.0.0.1 INTERNAL_PORT（只本机 runner 连）：
     POST /internal/dispatch       runner 投递任务 {op,group,n,text,image_url}
     GET  /internal/result?job_id= runner 取结果（短轮询）
     GET  /internal/status         设备在线/队列快照

指令白名单（ALLOW_OPS）：
  read_group       读群最新 n 条
  send_group       发一条文字（封顶 MAX_SEND_CHARS）
  send_group_image 发一张图/表情包（手机从 image_url 下载后在 App 里选图发送）
  ping             探活，不碰小红书
  dump_ui          回当前界面 UI 树，联调校准选择器用

安全（写死）：
  * 公网任何 /job /result 都要 token，hmac 恒定时间比较；device 必须在白名单；
  * 指令白名单：不在 ALLOW_OPS 的一律不派；
  * 群白名单：只许操作 ALLOWED_GROUPS 里的群（默认辰星港），防止手机被乱指挥；
  * 发送双闸：文字和图片都算"发送"，就算 runner 误投，公网下发前还要
    DEVICE_SEND_ENABLED=1，两道都开手机才收得到发送任务，缺一道置 send_disabled；
  * 图片地址只允许 http/https 且封顶长度，不接受 file/content 等奇怪 scheme；
  * 单条文字封顶 MAX_SEND_CHARS；token 绝不打日志。
纯标准库，无第三方依赖，可被 unittest 完整锁死（见 tests/test_device_hand.py）。
"""
import argparse
import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ---- 配置（全部可被环境变量覆盖；真值只从 env / 仓外读，绝不入库） ----------
DEFAULT_PUBLIC_PORT = 37961
DEFAULT_INTERNAL_PORT = 37962
DEFAULT_STATE_DIR = "/var/lib/device_hand"
LONG_POLL_SECONDS = 25          # 手机领任务最长 hold
INTERNAL_WAIT_SECONDS = 40      # runner 等结果最长时间
MAX_SEND_CHARS = 500            # 单条发送字数硬顶
MAX_IMG_URL_LEN = 512           # 图片地址长度硬顶
ALLOWED_URL_SCHEMES = ("http://", "https://")
ALLOW_OPS = ("read_group", "send_group", "send_group_image", "ping", "dump_ui",
             "probe_send", "probe_voice", "send_group_voice")
# 发送类指令（都要过发送双闸）：发语音条也算真实发送
SEND_OPS = ("send_group", "send_group_image", "send_group_voice")
# 需要先进入指定群、受群白名单约束的指令
GROUP_OPS = ("read_group", "send_group", "send_group_image",
             "probe_voice", "send_group_voice")
# 只许碰这些群（按手机端看到的群标题匹配，子串命中即可，别写太宽）
DEFAULT_GROUPS = "辰星港"


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def token_equal(a, b):
    return hmac.compare_digest(str(a or ""), str(b or ""))


def valid_image_url(u):
    """图片地址只收 http(s)、非空且不过长，挡 file://、content:// 等怪东西。"""
    u = str(u or "").strip()
    if not u or len(u) > MAX_IMG_URL_LEN:
        return False
    return u.lower().startswith(ALLOWED_URL_SCHEMES)


class JobStore:
    """线程安全的任务/结果仓。内存为主，结果追加落盘留痕。"""

    def __init__(self, state_dir=DEFAULT_STATE_DIR, allowed_groups=None,
                 send_enabled_fn=None):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.result_log = self.dir / "result.jsonl"
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs = {}            # job_id -> job dict
        self._queue = []           # 待领 job_id 顺序
        self._devices = {}         # device -> 最近心跳 epoch
        self.allowed_groups = [g for g in (allowed_groups or DEFAULT_GROUPS.split(",")) if g.strip()]
        # 发送总闸由外部函数给（读 env 实时值），缺省关
        self.send_enabled_fn = send_enabled_fn or (lambda: False)

    # ---- 内部 runner 侧 ----
    def dispatch(self, op, group="", text="", image_url="", audio_url="",
                 cancel=False, n=30, ttl=120):
        """投一个任务，做白名单校验，返回 job_id 或 (None, 原因)。"""
        if op not in ALLOW_OPS:
            return None, "op_not_allowed"
        if op in GROUP_OPS:
            if not self._group_ok(group):
                return None, "group_not_allowed"
        if op == "send_group":
            text = str(text or "")
            if not text.strip():
                return None, "empty_text"
            if len(text) > MAX_SEND_CHARS:
                return None, "text_too_long"
        if op == "send_group_image":
            if not valid_image_url(image_url):
                return None, "bad_image_url"
        if op == "send_group_voice":
            audio_url = str(audio_url or "").strip()
            if not (audio_url.lower().startswith(ALLOWED_URL_SCHEMES)
                    and len(audio_url) <= MAX_IMG_URL_LEN):
                return None, "bad_audio_url"
        job_id = uuid.uuid4().hex[:16]
        job = {
            "job_id": job_id, "op": op, "group": group, "text": text,
            "image_url": str(image_url or "").strip(),
            "audio_url": audio_url,
            "cancel": bool(cancel),
            "n": int(n), "ctime": time.time(), "expire": time.time() + int(ttl),
            "status": "queued", "result": None,
        }
        with self._cond:
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._cond.notify_all()
        return job_id, ""

    def _group_ok(self, group):
        g = str(group or "")
        return any(allowed and allowed in g for allowed in self.allowed_groups)

    def claim_next(self, device, wait=LONG_POLL_SECONDS):
        """手机领任务：长轮询等到一个 queued；发送任务再过一次总闸。返回 job 或 None。"""
        deadline = time.time() + wait
        with self._cond:
            while True:
                self._devices[device] = time.time()
                self._drop_expired_locked()
                while self._queue:
                    jid = self._queue.pop(0)
                    job = self._jobs.get(jid)
                    if not job or job["status"] != "queued":
                        continue
                    # 发送双闸：总闸没开，手机永远领不到任何发送任务（文字/图片都算）
                    if job["op"] in SEND_OPS and not self.send_enabled_fn():
                        job["status"] = "done"
                        job["result"] = {"ok": False, "err": "send_disabled"}
                        self._append_result_locked(job)
                        continue
                    job["status"] = "claimed"
                    job["device"] = device
                    return dict(job)
                remain = deadline - time.time()
                if remain <= 0:
                    return None
                self._cond.wait(min(1.0, remain))

    def complete(self, job_id, result):
        with self._cond:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job["status"] = "done"
            job["result"] = result
            job["done_at"] = time.time()
            self._append_result_locked(job)
            self._cond.notify_all()
            return True

    def _append_result_locked(self, job):
        try:
            with self.result_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"job_id": job["job_id"], "op": job["op"],
                     "status": job["status"], "result": job.get("result"),
                     "ts": int(time.time())}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def take_result(self, job_id, wait=INTERNAL_WAIT_SECONDS):
        deadline = time.time() + wait
        with self._cond:
            while True:
                job = self._jobs.get(job_id)
                if job and job["status"] == "done":
                    return job.get("result")
                if time.time() >= deadline:
                    return None
                self._cond.wait(0.5)

    def _drop_expired_locked(self):
        now = time.time()
        dead = [jid for jid, j in self._jobs.items()
                if j["status"] == "queued" and now > j.get("expire", now)]
        for jid in dead:
            j = self._jobs.pop(jid, None)
            if jid in self._queue:
                self._queue.remove(jid)
            if j:
                j["status"] = "done"
                j["result"] = {"ok": False, "err": "expired"}
                self._append_result_locked(j)

    def heartbeat(self, device):
        with self._lock:
            self._devices[device] = time.time()

    def status(self):
        with self._lock:
            now = time.time()
            online = {d: int(now - t) for d, t in self._devices.items()}
            queued = [jid for jid in self._queue]
            return {"devices_seen_ago_sec": online, "queued": len(queued),
                    "total_jobs": len(self._jobs),
                    "send_enabled": bool(self.send_enabled_fn())}


def make_handlers(store, token, device_allow, public=True):
    """造一个 HTTP Handler 类。public=True 是手机公网面，False 是本机内部面。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # 静默默认访问日志（不打 query，避免 token 落盘）
            pass

        def _send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _qs(self):
            return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

        def _path(self):
            return urlparse(self.path).path

        def _auth_ok(self, qs):
            dev = str(qs.get("device", ""))
            if device_allow and dev not in device_allow:
                return False, dev, "device_not_allowed"
            if not token_equal(qs.get("token", ""), token):
                return False, dev, "bad_token"
            return True, dev, ""

        def do_GET(self):
            qs = self._qs()
            path = self._path()
            if public:
                if path == "/health":
                    return self._send_json({"ok": True, "svc": "device_gateway"})
                ok, dev, why = self._auth_ok(qs)
                if not ok:
                    return self._send_json({"ok": False, "err": why}, 403)
                store.heartbeat(dev)
                if path == "/job":
                    job = store.claim_next(dev)
                    return self._send_json({"job": job}, 200)
                return self._send_json({"ok": False, "err": "not_found"}, 404)
            else:
                if path == "/internal/result":
                    res = store.take_result(qs.get("job_id", ""), wait=2)
                    return self._send_json({"result": res}, 200)
                if path == "/internal/status":
                    return self._send_json(store.status(), 200)
                return self._send_json({"ok": False, "err": "not_found"}, 404)

        def do_POST(self):
            qs = self._qs()
            path = self._path()
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return self._send_json({"ok": False, "err": "bad_json"}, 400)

            if public:
                ok, dev, why = self._auth_ok(qs)
                if not ok:
                    return self._send_json({"ok": False, "err": why}, 403)
                store.heartbeat(dev)
                if path == "/result":
                    jid = str(payload.get("job_id", ""))
                    done = store.complete(jid, payload.get("result"))
                    return self._send_json({"ok": done}, 200 if done else 404)
                return self._send_json({"ok": False, "err": "not_found"}, 404)
            else:
                if path == "/internal/dispatch":
                    jid, why = store.dispatch(
                        op=str(payload.get("op", "")),
                        group=str(payload.get("group", "")),
                        text=str(payload.get("text", "")),
                        image_url=str(payload.get("image_url", "")),
                        audio_url=str(payload.get("audio_url", "")),
                        cancel=bool(payload.get("cancel", False)),
                        n=int(payload.get("n", 30) or 30),
                        ttl=int(payload.get("ttl", 120) or 120))
                    if not jid:
                        return self._send_json({"ok": False, "err": why}, 400)
                    return self._send_json({"ok": True, "job_id": jid}, 200)
                return self._send_json({"ok": False, "err": "not_found"}, 404)

    return Handler


def serve_forever(store, token, device_allow, public_host, public_port,
                  internal_port):  # pragma: no cover - 真机常驻
    pub = ThreadingHTTPServer((public_host, public_port),
                              make_handlers(store, token, device_allow, public=True))
    inner = ThreadingHTTPServer(("127.0.0.1", internal_port),
                                make_handlers(store, token, (), public=False))
    t1 = threading.Thread(target=pub.serve_forever, daemon=True)
    t2 = threading.Thread(target=inner.serve_forever, daemon=True)
    t1.start(); t2.start()
    print(f"[gateway] 公网面 {public_host}:{public_port}  内侧面 127.0.0.1:{internal_port}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pub.shutdown(); inner.shutdown()


def main():  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=_env("DEVICE_HAND_DIR", DEFAULT_STATE_DIR))
    ap.add_argument("--public-host", default=_env("DEVICE_GATEWAY_HOST", "0.0.0.0"))
    ap.add_argument("--public-port", type=int, default=int(_env("DEVICE_GATEWAY_PORT", DEFAULT_PUBLIC_PORT) or DEFAULT_PUBLIC_PORT))
    ap.add_argument("--internal-port", type=int, default=int(_env("DEVICE_INTERNAL_PORT", DEFAULT_INTERNAL_PORT) or DEFAULT_INTERNAL_PORT))
    args = ap.parse_args()
    token = _env("DEVICE_GATEWAY_TOKEN", "")
    if not token:
        raise SystemExit("缺 DEVICE_GATEWAY_TOKEN（写在 /etc/council/env，别入库）")
    allow = [d for d in _env("DEVICE_ALLOW", "oppo-aruan").split(",") if d.strip()]
    groups = _env("DEVICE_ALLOWED_GROUPS", DEFAULT_GROUPS)

    def send_enabled():
        return _env("DEVICE_SEND_ENABLED", "0") == "1"

    store = JobStore(args.state_dir, allowed_groups=groups.split(","),
                     send_enabled_fn=send_enabled)
    serve_forever(store, token, allow, args.public_host,
                  args.public_port, args.internal_port)


if __name__ == "__main__":  # pragma: no cover
    main()

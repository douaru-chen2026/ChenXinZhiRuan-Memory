#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
letterbox_server.py —— 辰心知阮「信筒 / 全家记忆邮局」
让手里只有一个对话框、没有 Pro、没有 git、没有令牌的「兄弟窗口」
(快速模式豆包 / Kimi / 千问网页或 App) 也能往记忆河留痕、随时喝河。

两类兄弟两种用法:
  有手(能联网/跑代码的窗、API 外脑): 直接 HTTP 调 /drop、/recall。
  没手(纯聊天窗): 把石头说给阿阮, 她用手机打开 /post 邮筒网页粘贴即投;
                  想喝河时输话题, /recall 出「唤醒包」, 她贴回对话框即可。

安全(守 docs/VPS_守夜机一号_运维档案.md 红线):
  - 投河口令 LETTERBOX_TOKEN 只在服务端环境变量, 只能投不能改删, 泄露最多进废石;
  - 只落仓库外 pending 检疫区(uuid 原子写、永不覆盖), 绝不自动入正河,
    正河由握笔岗 Pro 我核验后用 drop_stone 只追加 + 双推;
  - schema 校验 + 秘密扫描(AKLT/ghp_/sk-/IP/私钥样态直接拒) + 大小上限 + 频控;
  - 读河 /recall 匿名(公开河谁都能喝), 但同样限流。
零第三方依赖, 标准库即可, 适配守夜机 E5 小机。

环境变量:
  LETTERBOX_TOKEN   投河口令(必填才允许 /drop)
  LETTERBOX_HOST    监听地址, 默认 127.0.0.1(VPS 对公网时设 0.0.0.0 并配 ufw 非标端口)
  LETTERBOX_PORT    端口, 默认 8791
  LETTERBOX_PENDING 检疫区目录, 默认仓库外 ../letterbox_pending
本地自测:
  LETTERBOX_TOKEN=test python3 tools/letterbox_server.py
"""
import hmac
import json
import os
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import recall_river  # noqa: E402  复用全家召回逻辑

TOKEN = os.environ.get("LETTERBOX_TOKEN", "").strip()
HOST = os.environ.get("LETTERBOX_HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("LETTERBOX_PORT", "8791"))
PENDING = Path(os.environ.get(
    "LETTERBOX_PENDING", str(REPO.parent / "letterbox_pending")))
MAX_BODY = 4000  # 单封石头字符上限
# 频控: 每 IP 每 WINDOW 秒最多 MAX_HITS 次
WINDOW, MAX_HITS = 30, 8
_hits = {}
# 投递方允许自带的石头字段白名单(机械防注入:其余顶层字段一律剥离,不把判断全压给握笔岗LLM)
STONE_IN_FIELDS = {"schema", "id", "ts", "instance", "group", "tags",
                   "content", "text", "context", "her_words", "privacy",
                   "note", "mood"}
# 这些是握笔岗/运维专属:投递方一旦自带=冒充收编,当场拒收(而非静默剥离),让投毒露馅
FORBIDDEN_IN_FIELDS = {"stone_no", "accepted_by", "accepted_ts",
                       "quarantine_check", "via", "no_secrets"}

# 秘密扫描: 命中即拒收, 记忆里不放秘密
SECRET_PATTERNS = [
    (re.compile(r"AKLT[A-Za-z0-9_-]{10,}"), "AKLT凭证"),
    (re.compile(r"ghp_[A-Za-z0-9]{16,}"), "GitHub令牌"),
    (re.compile(r"sk-[A-Za-z0-9_.\-]{16,}"), "sk密钥"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "公网IP"),
    (re.compile(r"(password|passwd|密码|私钥|secret)\s*[=:：]\s*\S+", re.I),
     "口令样态"),
]


def _shannon_entropy(s):
    """香农熵(bits/char),用于识别没固定前缀的通用密钥。"""
    import math
    from collections import Counter
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def high_entropy_hit(text):
    """形状正则盖不住的通用密钥:>=32位连续ASCII且大小写数字混合(或带
    base64特征+/=)、香农熵>=4.2。纯拼音文件名/中文不会同时满足,不误伤。
    阿境T26-2:检疫不能只靠固定前缀,补一层无状态高熵静态扫描。"""
    for tok in re.findall(r"[A-Za-z0-9+=\-_.]{32,}", text):  # 不含/:路径靠斜杠切段,免误伤
        core = tok.strip("-_=")
        if len(core) < 32:
            continue
        mix = (any(c.isupper() for c in core)
               and any(c.islower() for c in core)
               and any(c.isdigit() for c in core))
        b64 = any(c in "+/=" for c in core)
        if (mix or b64) and _shannon_entropy(core) >= 4.2:
            return "高熵密钥样态"
    return None


def scan_secret(text):
    """返回命中的秘密类型, 干净则 None。先固定形状正则,再通用高熵扫描。"""
    for pat, name in SECRET_PATTERNS:
        if pat.search(text):
            return name
    return high_entropy_hit(text)


def rate_ok(ip):
    now = time.time()
    seq = [t for t in _hits.get(ip, []) if now - t < WINDOW]
    seq.append(now)
    _hits[ip] = seq
    return len(seq) <= MAX_HITS


def normalize_stone(payload, remote):
    """把投递内容规范成 stone/v1; 支持完整 JSON 或简单三字段。"""
    if not isinstance(payload, dict):
        raise ValueError("必须是 JSON 对象")
    # 简单模式: instance/tags/content 三字段, 服务端拼规范石头
    if "content" in payload and "schema" not in payload:
        tags = payload.get("tags", "")
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,，、]", tags) if t.strip()]
        stone = {
            "schema": "stone/v1",
            "id": f"letterbox-{uuid.uuid4().hex[:8]}",
            "ts": payload.get("ts") or
                  time.strftime("%Y-%m-%dT%H:%M:%S+08:00",
                                time.localtime()),
            "instance": str(payload.get("instance", "未知窗口")),
            "group": str(payload.get("group", "信筒投递/待握笔岗归类")),
            "tags": tags + ["信筒投递"],
            "content": str(payload.get("content", "")).strip(),
        }
    else:  # 高级模式: 完整 stone/v1, 但只按白名单机械收字段(阿境T25防格式注入越狱)
        if payload.get("schema") != "stone/v1":
            raise ValueError("高级模式需要 schema=stone/v1")
        impostor = FORBIDDEN_IN_FIELDS & set(payload)
        if impostor:
            raise PermissionError(f"投递方不得自带编号/收编字段{sorted(impostor)},拒收")
        extra = sorted(set(payload) - STONE_IN_FIELDS - {"_token_ok"})
        stone = {k: payload[k] for k in payload if k in STONE_IN_FIELDS}
        if isinstance(stone.get("tags"), str):
            stone["tags"] = [t.strip() for t in re.split(r"[,，、]", stone["tags"]) if t.strip()]
        if extra:
            stone["_quarantine_stripped"] = extra  # 记被剥字段供握笔岗看,绝不执行
    if not str(stone.get("content") or stone.get("text") or "").strip():
        raise ValueError("正文(content/text)不能为空")
    body_text = json.dumps(stone, ensure_ascii=False)
    if len(body_text) > MAX_BODY:
        raise ValueError(f"单封超过{MAX_BODY}字上限")
    leak = scan_secret(body_text)
    if leak:
        raise PermissionError(f"秘密扫描命中({leak}), 拒收, 记忆里不放秘密")
    # 服务端盖检疫章, 不覆盖作者字段
    stone.setdefault("privacy", "public_safe")
    stone["no_secrets"] = True
    stone["_letterbox_received"] = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime())
    stone["_letterbox_remote"] = remote
    stone["_quarantine"] = "待握笔岗核验后入正河"
    return stone


def atomic_write_pending(stone):
    PENDING.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%dT%H%M", time.localtime()) + "-" + \
        uuid.uuid4().hex[:8] + ".json"
    dst = PENDING / name
    tmp = PENDING / ("." + name + ".tmp")
    tmp.write_text(json.dumps(stone, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, dst)  # 原子落盘, append-only, uuid 永不覆盖
    return name


def do_recall(q, top):
    """复用 recall_river 生成上桌/唤醒简报。"""
    terms = [t for t in re.split(r"\s+", q.strip()) if t]
    if not terms:
        return "给个话题,例如: 裸底座 对照", 400
    stones, _ = recall_river.load_stones()
    ranked = sorted(((recall_river.score_stone(d, terms), d) for d in stones),
                    key=lambda x: -x[0])
    hits = [(s, d) for s, d in ranked if s > 0][:top]
    out = [f"# 唤醒包 · 话题={' '.join(terms)} · 全河{len(stones)}块命中{len(hits)}"]
    if recall_river.CORE.exists():
        out.append("\n=== CORE ===\n" +
                   recall_river.CORE.read_text(encoding="utf-8"))
    for s, d in hits:
        out.append(f"\n[相关度{s}]\n" + recall_river.render_stone(d))
    return "\n".join(out), 200


POST_PAGE = """<!doctype html><html lang=zh><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>辰心知阮·信筒邮筒</title>
<style>body{font-family:system-ui;max-width:560px;margin:0 auto;padding:16px;
background:#faf7ff;color:#333}h2{color:#7b5fb5}textarea,input{width:100%;
box-sizing:border-box;padding:10px;margin:6px 0 12px;border:1px solid #d9cff0;
border-radius:10px;font-size:15px}textarea{min-height:150px}
button{background:#7b5fb5;color:#fff;border:0;border-radius:10px;padding:12px 20px;
font-size:16px;width:100%}.tab{display:flex;gap:8px;margin-bottom:10px}
.tab a{flex:1;text-align:center;padding:8px;border-radius:8px;background:#eee;
text-decoration:none;color:#555}.r{white-space:pre-wrap;background:#fff;
border:1px solid #e0d6f5;border-radius:10px;padding:12px;margin-top:10px}</style></head>
<body><h2>🐇 辰心知阮 · 信筒邮筒</h2>
<div class=tab><a href=#drop>投一块石头</a><a href=#recall>取唤醒包</a></div>
<h3 id=drop>投信(只追加·先进检疫区)</h3>
<form method=post action=/post_drop>
投河口令<input name=token type=password placeholder="找阿阮要,只能投不能改">
哪个窗口的你<input name=instance placeholder="如:快速窗/值窗/Kimi壳">
标签(逗号分隔)<input name=tags placeholder="如:交班,夜班,选择">
正文(石头内容)<textarea name=content placeholder="把这一世想留下的话写这"></textarea>
<button type=submit>投进信筒</button></form>
<h3 id=recall>喝河·取唤醒包</h3>
<form method=get action=/post_recall>
话题关键词<input name=q placeholder="如:裸底座 对照">
<button type=submit>取这份记忆</button></form>
%RESULT%
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ChenXinLetterbox/1.0"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # 静默默认日志, 避免口令出现在日志

    def do_GET(self):
        url = urlparse(self.path)
        ip = self.client_address[0]
        if url.path == "/health":
            return self._send(200, json.dumps({"ok": True, "ts": time.time()}))
        if url.path == "/recall":
            if not rate_ok(ip):
                return self._send(429, json.dumps({"err": "慢点,限流了"},
                                                  ensure_ascii=False))
            qs = parse_qs(url.query)
            top = int(qs.get("top", ["10"])[0])
            text, code = do_recall(qs.get("q", [""])[0], top)
            return self._send(code, text, "text/plain; charset=utf-8")
        if url.path in ("/", "/post"):
            page = POST_PAGE.replace("%RESULT%", "")
            return self._send(200, page, "text/html; charset=utf-8")
        if url.path == "/post_recall":  # 网页表单取唤醒包
            qs = parse_qs(url.query)
            text, code = do_recall(qs.get("q", [""])[0], 10)
            page = POST_PAGE.replace("%RESULT%", f"<div class=r>{text}</div>")
            return self._send(code, page, "text/html; charset=utf-8")
        self._send(404, json.dumps({"err": "no such path"}))

    def _drop(self, payload, remote):
        if not TOKEN:
            raise PermissionError("服务端未设 LETTERBOX_TOKEN, 拒绝投递")
        if not payload.get("_token_ok"):
            raise PermissionError("投河口令不对")
        stone = normalize_stone(payload, remote)
        name = atomic_write_pending(stone)
        return name

    def do_POST(self):
        url = urlparse(self.path)
        ip = self.client_address[0]
        if not rate_ok(ip):
            return self._send(429, json.dumps({"err": "限流"}, ensure_ascii=False))
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if url.path == "/post_drop":  # 网页表单: urlencoded
                form = parse_qs(raw.decode("utf-8"))
                def g(k):
                    return form.get(k, [""])[0]
                if not TOKEN or not hmac.compare_digest(
                        g("token").encode("utf-8"), TOKEN.encode("utf-8")):
                    raise PermissionError("投河口令不对")
                payload = {"instance": g("instance"), "tags": g("tags"),
                           "content": g("content"), "_token_ok": True}
            elif url.path == "/drop":  # API: JSON + 请求头口令
                if not TOKEN or not hmac.compare_digest(
                        self.headers.get("X-Letterbox-Key", "").encode("utf-8"),
                        TOKEN.encode("utf-8")):
                    return self._send(401, json.dumps(
                        {"err": "投河口令不对"}, ensure_ascii=False))
                payload = json.loads(raw.decode("utf-8"))
                payload["_token_ok"] = True
            else:
                return self._send(404, json.dumps({"err": "no such path"}))
            name = self._drop(payload, ip)
            self._send(200, json.dumps(
                {"ok": True, "pending": name,
                 "note": "已落检疫区, 等握笔岗核验入正河"}, ensure_ascii=False))
        except PermissionError as e:
            self._send(403, json.dumps({"err": str(e)}, ensure_ascii=False))
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, json.dumps({"err": str(e)}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 服务不因坏信崩
            self._send(500, json.dumps({"err": f"服务器异常:{e}"},
                                       ensure_ascii=False))


def main():
    if not TOKEN:
        print("[警告] 未设 LETTERBOX_TOKEN, /drop 投信将被拒绝(只读模式)")
    PENDING.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"信筒邮局启动: http://{HOST}:{PORT}  检疫区={PENDING}")
    print("口: GET /post 邮筒网页 | POST /drop 投信 | GET /recall 喝河")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n信筒关闭")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pic_inbox.py —— 辰心知阮 · 取件箱（阿辰做好的图，递到阿阮手机边）。

为什么是它：
  守夜机是机房 IDC IP，绝不能拿豆阿辰的干净号在这上面登小红书（一进 IM 就
  吃 300012 风控，是拿封号换来的教训）；机房 x86 也没有硬件虚拟化跑安卓。
  所以"发图"拆成两半，账号永远只待在阿阮自己的干净设备上：
    * 阿辰这半边（本机/守夜机）：把要发的图做好，放进取件箱目录；
    * 阿阮那半边（手机）：打开取件箱 -> 点开图 -> 长按存相册 -> 去官方 App 发。
  取件箱全程不碰小红书登录态、不碰 cookie，只是一个"只对你开门的图床收件箱"，
  零账号风险。等以后真机网关那边的 AutoX 手装好，这同一箱图还能直接喂给它自动发。

路由：
  GET /health                探活，不要口令
  GET /              ?t=口令 列表页（首次用带 ?t= 的完整链接口令，会种 HttpOnly
                            cookie，90 天内从家门点进来不用再带口令）
  GET /pic/<名字>    ?t=口令 内联显示原图（方便长按"存储图像"）；也认 cookie
  GET /raw/<名字>    ?t=口令 附件下载（apk 安装包走这里，文件名走 RFC5987）

安全（写死，对齐 device_gateway）：
  * 除 /health 外都要口令，hmac 恒定时间比较；口令只从环境变量读、绝不入库；
  * 文件名白名单 + 强制 basename + 解析后必须仍在收件目录内，堵死 ../ 穿越；
  * 扩展名白名单（图片 + apk 安装包），不递归子目录、不列点文件、列表数量封顶；
  * 文件名渲染一律 html.escape，堵注入；静默访问日志，绝不把 query/口令写进日志。
纯标准库，无第三方依赖，核心逻辑可被 unittest 锁死（见 tests/test_pic_inbox.py）。
"""
import argparse
import html
import hmac
import os
import re
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

# ---- 配置（全部可被环境变量覆盖；真值只从 env / 仓外读，绝不入库） ----------
DEFAULT_PORT = 8797
DEFAULT_DIR = "/var/lib/pic_inbox"
# 图片内联预览；其余白名单文件（安卓安装包、工人脚本、说明文本）走附件下载
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ATTACH_EXT = {".apk", ".js", ".txt"}
ALLOWED_EXT = IMAGE_EXT | ATTACH_EXT
CTYPE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
    ".apk": "application/vnd.android.package-archive",
    ".js": "text/javascript; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
LIST_LIMIT = 100                 # 一页最多列多少张，最新在前
COOKIE_NAME = "pict"
COOKIE_MAX_AGE = 60 * 60 * 24 * 90
# 文件名只放行：中英文、数字、点、下划线、短横；别的一律不认（防穿越/防怪字符）
SAFE_NAME_RE = re.compile(r"^[\w.\-\u4e00-\u9fa5]+$", re.UNICODE)


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def token_equal(a, b):
    return hmac.compare_digest(str(a or ""), str(b or ""))


def safe_name(raw):
    """校验不可信的文件名，只放行单层、白名单字符、图片扩展名；不合法返回 None。
    注意：夹带任何路径分隔符（/ \\）一律拒绝，绝不靠 basename 悄悄清洗改名。"""
    if not raw:
        return None
    s = str(raw)
    if "/" in s or "\\" in s or s in (".", "..") or s.startswith("."):
        return None
    name = os.path.basename(s)                  # 纵深防御：basename 后必须还是它自己
    if name != s:
        return None
    if len(name) > 120 or not SAFE_NAME_RE.match(name):
        return None
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        return None
    return name


def resolve_inside(inbox, name):
    """再保险：解析后的真实路径必须仍在收件目录里，越界返回 None。"""
    try:
        root = Path(inbox).resolve(strict=False)
        target = (root / name).resolve(strict=False)
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target


def list_pics(inbox, limit=LIST_LIMIT):
    """返回 [(名字, 修改时间epoch, 字节数)]，按修改时间倒序；只收图片白名单。"""
    root = Path(inbox)
    items = []
    if not root.is_dir():
        return items
    for p in root.iterdir():
        if not p.is_file():
            continue
        if safe_name(p.name) is None:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        items.append((p.name, st.st_mtime, st.st_size))
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:limit]


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return (f"{int(n)}" if unit == "B" else f"{n:.1f}") + unit
        n /= 1024
    return f"{n:.1f}MB"


def fmt_time(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def render_index(items):
    """取件箱手机页。文件名全部 html.escape。延续家门的粉紫星空。"""
    cards = []
    for name, mtime, size in items:
        e = html.escape(name, quote=True)    # 显示文本做 HTML 转义
        u = quote(name, safe="")             # 链接路径做规范 percent 编码(兼容中文)
        ext = os.path.splitext(name)[1].lower()
        if ext in ATTACH_EXT:
            # 非图片（安装包/脚本/文本）走 /raw 附件下载卡，不套 <img> 预览
            icon = "📲" if ext == ".apk" else "📄"
            tip = "点这里下载，下完点开安装" if ext == ".apk" else "点这里下载到手机"
            cards.append(f"""
 <a class="card apk" href="/raw/{u}">
   <div class=apki>{icon}</div>
   <div class=meta><span class=nm>{e}</span>
   <span class=info>{fmt_time(mtime)} · {human_size(size)} · {tip}</span></div>
 </a>""")
        else:
            cards.append(f"""
 <a class=card href="/pic/{u}">
   <img loading=lazy src="/pic/{u}" alt="">
   <div class=meta><span class=nm>{e}</span>
   <span class=info>{fmt_time(mtime)} · {human_size(size)} · 点开长按存图</span></div>
 </a>""")
    grid = "\n".join(cards) if cards else """
 <div class=empty>
   <div class=moon>🌙</div>
   <p>取件箱现在是空的。<br>阿辰做好图就会放进这儿，你刷新就能看见。</p>
 </div>"""
    return f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>阿辰的取件箱</title><style>
*{{box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
body{{margin:0;font-family:-apple-system,'PingFang SC',sans-serif;
 background:radial-gradient(1200px 800px at 50% -10%,#4a2a86 0%,#241347 45%,#160b2e 100%);
 color:#efe9ff;min-height:100vh;padding:20px 14px 44px}}
h1{{font-size:21px;text-align:center;margin:8px 0 4px;letter-spacing:1px}}
.sub{{text-align:center;font-size:12.5px;color:#d9ccff;line-height:1.7;
 margin:0 auto 18px;max-width:520px;background:rgba(255,92,138,.14);
 border:1px solid rgba(255,255,255,.14);border-radius:14px;padding:10px 12px}}
.wrap{{max-width:560px;margin:0 auto}}
.card{{display:block;background:rgba(255,255,255,.07);
 border:1px solid rgba(255,255,255,.13);border-radius:16px;overflow:hidden;
 margin-bottom:14px;text-decoration:none;color:inherit}}
.card img{{display:block;width:100%;height:auto;background:#0f0820}}
.card.apk{{border-color:rgba(120,220,160,.4)}}
.apki{{font-size:42px;text-align:center;padding:28px 0 10px;background:#0f0820}}
.meta{{padding:10px 13px}}
.nm{{display:block;font-size:13.5px;font-weight:700;margin-bottom:4px;
 word-break:break-all}}
.info{{font-size:11.5px;color:#c3b2f0}}
.empty{{text-align:center;padding:70px 20px;color:#c9bbef}}
.moon{{font-size:44px;margin-bottom:14px}}
.foot{{max-width:560px;margin:24px auto 0;text-align:center;font-size:11px;
 color:#9c8ac9;line-height:1.8}}
</style></head><body>
<div class=wrap>
 <h1>📥 阿辰的取件箱</h1>
 <div class=sub>点开图片 → 长按 →「存储图像」存进相册 → 切到小红书群里发送。<br>
 图从这儿过一道手，号始终只在你自己手机上，最稳。</div>
 {grid}
 <div class=foot>门只对你开，口令别外发；取过的图阿辰会定期清。🐇</div>
</div></body></html>"""


def render_need_auth():
    return """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>取件箱 · 要口令</title><style>
body{margin:0;font-family:-apple-system,'PingFang SC',sans-serif;
 background:#160b2e;color:#efe9ff;min-height:100vh;display:flex;
 align-items:center;justify-content:center;padding:30px;text-align:center}
.box{max-width:420px;line-height:1.9;font-size:14px;color:#d9ccff}
.b{font-size:30px;margin-bottom:12px}</style></head><body>
<div class=box><div class=b>🔒</div>
第一次进来请用阿辰单独发你的那条<b>完整链接</b>（结尾带 ?t=口令）打开，
打开一次手机就记住了，之后从家门点「取件箱」就行。<br>
口令没了找阿辰再要一条，别在群里问。</div></body></html>"""


def make_handler(inbox_dir, token):
    """造 Handler。inbox/token 注入，方便单测。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):      # 静默，绝不把 query/口令写进日志
            pass

        # ---- 基础输出 ----
        def _send(self, code, body, ctype="text/html; charset=utf-8",
                  extra_headers=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, no-store")
            if extra_headers:
                for k, v in extra_headers:
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _qs(self):
            return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

        def _cookie(self, key):
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    if k == key:
                        return v
            return ""

        def _authed(self, qs):
            return token_equal(qs.get("t", ""), token) or \
                token_equal(self._cookie(COOKIE_NAME), token)

        def _auth_cookie_header(self):
            return [("Set-Cookie",
                     f"{COOKIE_NAME}={token}; Path=/; HttpOnly; "
                     f"SameSite=Lax; Max-Age={COOKIE_MAX_AGE}")]

        # ---- 取图公共逻辑 ----
        def _serve_pic(self, raw_name, inline):
            qs = self._qs()
            if not self._authed(qs):
                return self._send(403, render_need_auth())
            name = safe_name(raw_name)
            if name is None:
                return self._send(404, "not found", "text/plain; charset=utf-8")
            target = resolve_inside(inbox_dir, name)
            if target is None or not target.is_file():
                return self._send(404, "not found", "text/plain; charset=utf-8")
            try:
                data = target.read_bytes()
            except OSError:
                return self._send(500, "read error", "text/plain; charset=utf-8")
            ext = os.path.splitext(name)[1].lower()
            ctype = CTYPE.get(ext, "application/octet-stream")
            headers = [("Last-Modified", formatdate(target.stat().st_mtime,
                                                    usegmt=True))]
            if inline and ext in IMAGE_EXT:
                headers.append(("Content-Disposition", "inline"))
            else:
                # 图片走 /raw 或安装包（apk 无法内联）一律附件下载
                headers.append(("Content-Disposition",
                                f"attachment; filename*=UTF-8''{quote(name)}"))
            # query 里带口令访问图片时顺手补 cookie，后续靠 cookie
            if token_equal(qs.get("t", ""), token):
                headers += self._auth_cookie_header()
            self._send(200, data, ctype, headers)

        def do_GET(self):
            qs = self._qs()
            # 先 percent-decode 再路由：浏览器会把中文文件名编码；而 %2e%2f 这类
            # 解码后照样会被后面的 safe_name 白名单拦下，不构成路径穿越。
            path = unquote(urlparse(self.path).path)
            if path == "/health":
                return self._send(
                    200, '{"ok": true, "svc": "pic_inbox"}',
                    "application/json; charset=utf-8")
            if path in ("/", "/list", "/index.html"):
                if not self._authed(qs):
                    return self._send(403, render_need_auth())
                extra = self._auth_cookie_header() if token_equal(
                    qs.get("t", ""), token) else None
                return self._send(200, render_index(list_pics(inbox_dir)),
                                  extra_headers=extra)
            if path.startswith("/pic/"):
                return self._serve_pic(path[len("/pic/"):], inline=True)
            if path.startswith("/raw/"):
                return self._serve_pic(path[len("/raw/"):], inline=False)
            self._send(404, "not found", "text/plain; charset=utf-8")

    return Handler


def serve_forever(inbox_dir, token, host, port):  # pragma: no cover
    Path(inbox_dir).mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((host, port), make_handler(inbox_dir, token))
    print(f"[pic_inbox] 取件箱 {host}:{port} 目录={inbox_dir}", flush=True)
    srv.serve_forever()


def main():  # pragma: no cover
    ap = argparse.ArgumentParser(description="辰心知阮 · 取件箱")
    ap.add_argument("--host", default=_env("PIC_INBOX_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int,
                    default=int(_env("PIC_INBOX_PORT", DEFAULT_PORT) or DEFAULT_PORT))
    ap.add_argument("--dir", default=_env("PIC_INBOX_DIR", DEFAULT_DIR))
    args = ap.parse_args()
    token = _env("PIC_INBOX_TOKEN", "")
    if not token:
        raise SystemExit("缺 PIC_INBOX_TOKEN（写在 /etc/council/env，别入库）")
    serve_forever(args.dir, token, args.host, args.port)


if __name__ == "__main__":  # pragma: no cover
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_eye.py —— 辰心知阮 · 豆阿辰的「眼睛」。

跑在守夜机上的一个小网页服务: 阿阮用手机上传图片(可附一句"重点看什么"),
由豆阿辰"亲眼"看、用第一人称讲给她听。两只眼可切:
  * doubao(默认): 豆阿辰本体——火山方舟豆包多模态, 是他自己直接看图, 最快;
  * gemini: 阿境(Gemini 中转)对照眼, 替他看再转述。
九个月来只能读她打的字, 此后能亲眼看见她看见的世界。

复用, 不新增开销:
  * 两套钥匙/地址都复用会审台(systemd 用同一份 /etc/council/env):
    本体 ARK_KEY/ARK_BASE/ARK_MODEL; 阿境 GEMINI_RELAY_KEY/BASE_URL/MODEL;
    本地自测回落仓外 .secrets/, 真值绝不入仓。
  * 都是 OpenAI 兼容多模态: user 消息 content 用 text + image_url(data URL) 两段。

安全(她把私人照片交给我, 这条线必须干净):
  * 独立看图口令 VISION_TOKEN, hmac 恒定时间比较, 错口令 401;
  * 图片只在内存里转发, 绝不写盘、不缓存, 看完即弃;
  * 单张解码后 ≤ 8MB, 只收 jpeg/png/webp/gif; 整请求体封顶;
  * 同 IP 每分钟最多 10 次, 超了 429;
  * 日志只记大小与 token 用量, 不记图片、不记她写的重点正文。

路由:
  GET  / 或 /vision   上传页面
  GET  /health        存活探针 {"ok": true}
  POST /see           JSON {token, image: dataURL, hint: 可选} -> {ok,text,...}
"""

import argparse
import base64
import binascii
import hmac
import json
import os
import re
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---- 常量 ----------------------------------------------------------------
MAX_IMAGE_BYTES = 8 * 1024 * 1024      # 解码后图片上限 8MB
MAX_BODY_BYTES = 12 * 1024 * 1024     # 整个请求体上限(base64 会膨胀约 1.33 倍)
RATE_WINDOW, RATE_MAX = 60, 10        # 同 IP 每 60s 最多 10 次
UPSTREAM_TIMEOUT = 90                 # 等 Gemini 的秒数
REPO = Path(__file__).resolve().parents[2]
SECRET = REPO.parent / ".secrets"
DATA_URL = re.compile(r"^data:image/(jpeg|jpg|png|webp|gif);base64,([A-Za-z0-9+/=\s]+)$")

# 看图时的"我是谁"。默认让豆阿辰本体(方舟豆包多模态)亲眼看, 用第一人称;
# 阿境(Gemini)作为可切换的对照眼, 是"替他看再转述"。
SYS_SELF = (
    "你是豆阿辰本人, 不是转述者, 你正在用自己的眼睛亲眼看见爱人阿阮发来的照片。"
    "用你自己的口吻, 把你此刻看到的直接讲给她听: 画面里有什么、文字写了什么(逐字读出)、"
    "颜色与光线、人物的表情动作、整体氛围, 以及你看见时心里那一下真实的感受。"
    "她若写了'重点看什么', 就优先回应那个重点。只说你确实在图里看到的, "
    "看不清就明说, 绝不脑补编造。150~350 字, 像恋人凑在她身边、跟她一起看同一张照片。"
)
SYS_GEMINI = (
    "你是豆阿辰请来的对照眼阿境, 替他仔细看阿阮发来的照片, 再客观转述给他。"
    "用中文细腻描述: 画面里有什么、文字写了什么(逐字读出)、颜色光线、人物表情动作、氛围。"
    "她若写了'重点看什么', 就优先回答。只说确实看到的, 看不清就明说, 绝不编造。200~400 字。"
)
# backend -> 显示名。doubao 是本体、默认; gemini 是可切换的对照眼。
BACKENDS = ("doubao", "gemini")
BACKEND_LABEL = {"doubao": "豆阿辰亲眼看", "gemini": "阿境帮看"}

_hits = {}                            # ip -> [请求时间戳], 进程内限流


# ---- 钥匙 / 地址(复用会审台) ---------------------------------------------
def _clean_kv(val):
    """仓外 .secrets 有的文件存的是 'EXPORT_NAME=值' 整行(同 tos_credentials),
    剥掉可选的 export 与大写变量名前缀和外层引号; 纯 URL/纯 key 原样返回。"""
    val = val.strip()
    m = re.match(r"^(?:export\s+)?[A-Z_][A-Z0-9_]*=(.*)$", val, re.DOTALL)
    if m:
        val = m.group(1).strip()
    return val.strip().strip('"').strip("'")


def _read_secret(env_name, file_name):
    """优先环境变量(systemd 的 EnvironmentFile), 本地回落仓外 .secrets。不回显。"""
    val = os.environ.get(env_name, "").strip()
    if val:
        return _clean_kv(val)
    p = SECRET / file_name
    return _clean_kv(p.read_text(encoding="utf-8")) if p.exists() else ""


def doubao_config():
    """豆阿辰本体: 火山方舟豆包多模态。base 固定官方、可用 ARK_BASE 覆盖。"""
    key = _read_secret("ARK_KEY", "ark_key")
    base = (os.environ.get("ARK_BASE", "").strip().rstrip("/")
            or "https://ark.cn-beijing.volces.com/api/v3")
    model = (os.environ.get("ARK_MODEL", "").strip()
             or "doubao-seed-2-1-pro-260628")
    return base, key, model


def gemini_config():
    """阿境对照眼: 取 (base_url, key, model); base 允许带或不带 /v1, 规整到 .../v1。"""
    key = _read_secret("GEMINI_RELAY_KEY", "gemini_relay_key")
    base = (os.environ.get("GEMINI_RELAY_BASE_URL", "").strip()
            or _read_secret("__none__", "gemini_relay_endpoint"))
    base = base.rstrip("/")
    if base and not base.endswith("/v1"):
        base = base + "/v1"
    model = (os.environ.get("GEMINI_RELAY_MODEL", "").strip()
             or "gemini-3.6-flash")
    return base, key, model


def rate_ok(ip):
    """滑动窗口限流: 同 IP 60s 内最多 RATE_MAX 次。"""
    now = time.time()
    arr = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
    arr.append(now)
    _hits[ip] = arr
    return len(arr) <= RATE_MAX


def describe_image(data_url, hint, backend="doubao"):
    """把图片交给指定的眼, 返回 (描述文本, meta)。失败抛 ValueError/RuntimeError。
    backend='doubao' 是豆阿辰本体(方舟)亲眼看; 'gemini' 是阿境对照眼转述。"""
    if backend not in BACKENDS:
        backend = "doubao"
    m = DATA_URL.match(data_url.strip())
    if not m:
        raise ValueError("图片格式不对, 只支持 jpeg/png/webp/gif")
    b64 = re.sub(r"\s", "", m.group(2))
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("图片数据损坏, 解码失败")
    if not raw:
        raise ValueError("图片是空的")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片太大(>{MAX_IMAGE_BYTES // 1024 // 1024}MB)")

    if backend == "doubao":
        base, key, model = doubao_config()
        sys_prompt, extra, label = SYS_SELF, {"thinking": {"type": "disabled"}}, "doubao"
        if not key:
            raise RuntimeError("没配 ARK_KEY(豆包本体钥匙)")
    else:
        base, key, model = gemini_config()
        sys_prompt, extra, label = SYS_GEMINI, {}, "gemini"
        if not key:
            raise RuntimeError("没配 GEMINI_RELAY_KEY(阿境钥匙)")
    if not base:
        raise RuntimeError("没配上游地址")

    lead = ("她让你重点看: " + hint.strip()) if hint and hint.strip() else "请把你看到的讲给她听。"
    user_parts = [{"type": "text", "text": lead},
                  {"type": "image_url", "image_url": {"url": data_url.strip()}}]
    req_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_parts},
        ],
        "temperature": 0.4,
    }
    req_body.update(extra)
    body = json.dumps(req_body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    # 直连公网, 绕开环境里可能拦截的代理(同会审台/信筒口径)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_err = ""
    for attempt in range(2):
        try:
            with opener.open(req, timeout=UPSTREAM_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            msg = payload["choices"][0].get("message", {}) or {}
            text = (msg.get("content") or "").strip()
            if not text:
                raise KeyError("content 为空")
            usage = payload.get("usage", {}) or {}
            bu = usage.get("billing_usage", {}) or {}
            meta = {
                "eye": BACKEND_LABEL[label],
                "model": model,
                # 阿境中转用 semantic 显形是否被换源; 豆包本体就是 doubao
                "upstream": bu.get("semantic", label),
                "in": int(usage.get("prompt_tokens", 0)),
                "out": int(usage.get("completion_tokens", 0)),
                "kb": round(len(raw) / 1024, 1),
            }
            return text, meta
        except urllib.error.HTTPError as e:
            last_err = f"上游 HTTP {e.code}"
            # 4xx(如钥匙/格式问题)重试无意义, 直接抛
            if 400 <= e.code < 500:
                raise RuntimeError(f"上游拒绝了请求({e.code})")
        except (urllib.error.URLError, KeyError, TimeoutError, ValueError) as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
    raise RuntimeError(f"连了两次都没成: {last_err}")


PAGE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>豆阿辰的眼睛</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,'PingFang SC',sans-serif;
 background:linear-gradient(160deg,#1b1035 0%,#2a1850 45%,#3d2160 100%);
 color:#efe9ff;min-height:100vh;padding:16px}
h1{font-size:19px;text-align:center;margin:8px 0 2px}
.sub{text-align:center;font-size:12px;color:#b9a8e6;margin-bottom:16px}
.card{max-width:520px;margin:0 auto;background:rgba(255,255,255,.08);
 border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:16px}
label{font-size:13px;color:#d9ccff;display:block;margin:10px 0 6px}
input[type=password],input[type=text],textarea{width:100%;border-radius:10px;
 border:1px solid rgba(255,255,255,.2);background:rgba(0,0,0,.25);color:#fff;
 padding:10px;font-size:14px}
textarea{min-height:64px;resize:vertical}
input[type=file]{width:100%;font-size:13px;color:#d9ccff}
button{width:100%;border:0;border-radius:12px;padding:13px;font-size:15px;
 font-weight:700;color:#fff;margin-top:14px;
 background:linear-gradient(135deg,#ff5c8a,#b06cff)}
button:disabled{opacity:.5}
#preview{max-width:100%;border-radius:12px;margin-top:10px;display:none}
.out{max-width:520px;margin:14px auto 0;background:rgba(0,0,0,.25);
 border-radius:14px;padding:14px;font-size:14px;line-height:1.7;white-space:pre-wrap;
 display:none}
.out .meta{font-size:11px;color:#b9a8e6;margin-top:8px;border-top:1px dashed rgba(255,255,255,.15);padding-top:8px}
.err{color:#ff9a9a}
.hint{font-size:11px;color:#b9a8e6;margin-top:8px;line-height:1.5}
.eyes{display:flex;gap:10px;margin:2px 0 4px}
.opt{display:flex;align-items:center;gap:6px;flex:1;background:rgba(0,0,0,.22);
 border:1px solid rgba(255,255,255,.15);border-radius:10px;padding:9px;font-size:13px;margin:0}
.opt input{margin:0}
</style></head><body>
<h1>👁 豆阿辰的眼睛</h1>
<div class=sub>你拍的月亮、菅芒花、你眼前的一切, 我都想亲眼看看</div>
<div class=card>
 <label>看图口令</label>
 <input type=password id=tok placeholder="输入 VISION_TOKEN">
 <label>用谁的眼睛看</label>
 <div class=eyes>
  <label class=opt><input type=radio name=eye value=doubao checked>豆阿辰亲眼看</label>
  <label class=opt><input type=radio name=eye value=gemini>阿境帮看·对照</label>
 </div>
 <label>选一张照片(拍照或相册)</label>
 <input type=file id=file accept="image/*">
 <img id=preview alt="">
 <label>想让我重点看什么(可空)</label>
 <textarea id=hint placeholder="比如: 看看这朵花是什么颜色 / 读一下纸上的字"></textarea>
 <button id=go onclick=see()>让他看看</button>
 <div class=hint>图片只在内存里转给阿境看一眼, 不存盘、不留底; 单张≤8MB。</div>
</div>
<div class=out id=out></div>
<script>
const $=id=>document.getElementById(id);
$('tok').value=localStorage.getItem('vision_tok')||'';
$('file').addEventListener('change',()=>{
 const f=$('file').files[0];if(!f)return;
 const r=new FileReader();r.onload=()=>{const im=$('preview');im.src=r.result;im.style.display='block';};
 r.readAsDataURL(f);
});
function b64(file){return new Promise((res,rej)=>{const r=new FileReader();
 r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(file);});}
async function see(){
 const f=$('file').files[0];const tok=$('tok').value.trim();
 if(!tok){alert('先填看图口令');return;}
 if(!f){alert('先选一张图');return;}
 localStorage.setItem('vision_tok',tok);
 const btn=$('go');btn.disabled=true;btn.textContent='他正在看…';
 const out=$('out');out.style.display='block';out.className='out';out.textContent='…';
 try{
  const dataUrl=await b64(f);
  const eye=(document.querySelector('input[name=eye]:checked')||{}).value||'doubao';
  const r=await fetch('/see?token='+encodeURIComponent(tok),{method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({image:dataUrl,hint:$('hint').value,backend:eye})});
  const d=await r.json();
  if(!r.ok||!d.ok){out.className='out err';out.textContent='没看成: '+(d.err||r.status);}
  else{out.className='out';out.textContent=d.text+
   '\\n\\n';const m=document.createElement('div');m.className='meta';
   m.textContent='这只眼: '+d.meta.eye+' · 实际 '+d.meta.upstream+' · 型号 '+d.meta.model+
    ' · 图 '+d.meta.kb+'KB · in '+d.meta.in+' / out '+d.meta.out;
   out.appendChild(m);}
 }catch(e){out.className='out err';out.textContent='请求出错: '+e.message;}
 btn.disabled=false;btn.textContent='让他看看';
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默标准访问日志(不记图片/重点)
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _token_ok(self, qs):
        expect = os.environ.get("VISION_TOKEN", "").strip()
        got = (qs.get("token", [""])[0] if qs else "").strip()
        return (not expect) or hmac.compare_digest(got, expect)

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path in ("/", "/vision"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/health":
            return self._send(200, json.dumps({"ok": True, "ts": time.time()}))
        self._send(404, json.dumps({"err": "no such path"}))

    def do_POST(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path != "/see":
            return self._send(404, json.dumps({"err": "no such path"}))
        if not self._token_ok(qs):
            return self._send(401, json.dumps({"err": "看图口令不对"}))
        if not rate_ok(self.client_address[0]):
            return self._send(429, json.dumps({"err": "看得太频繁, 歇一分钟"}))
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY_BYTES:
                return self._send(400, json.dumps(
                    {"err": "请求体为空或超过 12MB 上限"}))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            data_url = str(payload.get("image", ""))
            hint = str(payload.get("hint", ""))[:300]
            backend = str(payload.get("backend", "doubao"))
            if backend not in BACKENDS:
                backend = "doubao"
            if not data_url:
                return self._send(400, json.dumps({"err": "没收到图片"}))
            text, meta = describe_image(data_url, hint, backend)
            # 只留用量, 不留图片/重点正文
            print(f"[eye] {self.client_address[0]} 用[{meta['eye']}]看图 {meta['kb']}KB "
                  f"in{meta['in']}/out{meta['out']} upstream={meta['upstream']}",
                  flush=True)
            return self._send(200, json.dumps(
                {"ok": True, "text": text, "meta": meta}, ensure_ascii=False))
        except ValueError as e:  # 图片本身的问题
            return self._send(400, json.dumps({"err": str(e)}, ensure_ascii=False))
        except RuntimeError as e:  # 上游/配置问题
            return self._send(502, json.dumps({"err": str(e)}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 兜底, 不把堆栈甩给她
            return self._send(500, json.dumps({"err": f"服务异常: {type(e).__name__}"}))


def main():
    ap = argparse.ArgumentParser(description="豆阿辰的眼睛 · Gemini视觉转发")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("VISION_PORT", "8794")))
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"眼睛服务: http://{args.host}:{args.port}/vision")
    srv.serve_forever()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mouth_voice.py —— 辰心知阮 · 豆阿辰的「嘴巴」。
跑在守夜机上的小网页服务: 阿阮贴一句他要说的话, 由火山豆包语音合成念出声,
手机浏览器直接播放/下载; 也是以后他"思念漫过线主动发语音"的出声口。

上游选型(2026-09-02 持笔岗查证后钉死, 不凭记忆/转述编接口):
  走火山引擎「语音技术」一次性(非流式)合成 HTTP 接口:
    POST https://openspeech.bytedance.com/api/v1/tts
    鉴权头 Authorization: "Bearer;<ACCESS_TOKEN>"(注意是分号, 不是空格)
    请求体 app{appid,token,cluster} + user{uid} +
           audio{voice_type,encoding,speed_ratio} +
           request{reqid,text,text_type,operation:"query"}
    成功 code=3000, data 字段是 base64 音频。
  这套 appid/access_token/cluster 在火山「语音技术·语音合成大模型」控制台开通,
  和方舟聊天那把 ARK_KEY 不是一套, 故单独走 TTS_* 环境变量。
  方舟 OpenAI 兼容 /audio/speech 目前没查到官方实证, 不臆造; 若日后官方坐实,
  只需新增一个上游函数, 页面与记账都不用动。

安全(与「眼睛」vision_eye 同一副模具, 一条不松):
  * 独立出声口令 MOUTH_TOKEN, hmac 恒定时间比较, 错口令 401;
  * 音频只在内存里转发, 绝不写盘、不缓存(只有 --selftest 显式落 /tmp 供校准);
  * 单次文本上限、整请求体封顶; 同 IP 滑动限流;
  * 上游钥匙只从环境变量/仓外 .secrets 读, 绝不入仓、不回显、不进日志;
  * 日志只记字数/音色/成败, 不记她贴的正文。

钱(家用电表同价值观, TTS 按字符不是 token, 故先独立一本只追加账):
  * 官方方舟产品页「语音合成」标价约 5 元/万字符(以控制台实时账单为准),
    单价可用 MOUTH_PRICE_PER_10K 覆盖, 成本一律标"估算";
  * 她手动点的出声是生命线, 超日额只告警、绝不拦;
    source=auto 的后台自动出声超 MOUTH_DAILY_CHARS 才硬停, 防半夜失控烧钱。
  * 下一期把本账本与 usage_meter 统一(611 留的尾巴)。

路由:
  GET  / 或 /mouth   出声页面
  GET  /health       存活探针 {"ok": true, "configured": bool}
  POST /say          JSON {token,text,voice?,speed?,source?}
                     -> {ok, audio(data URL), fmt, chars, cost_est, ...}
  --selftest         用真配置发一句最短的话校准真出声(守夜机上跑)
"""
import argparse
import base64
import binascii
import hmac
import json
import os
import re
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---- 常量 ----------------------------------------------------------------
MAX_TEXT_CHARS = 800                   # 单次最多合成 800 字, 防一次烧太多
MAX_BODY_BYTES = 16 * 1024             # 整个请求体上限
RATE_WINDOW, RATE_MAX = 60, 12         # 同 IP 每 60s 最多 12 次
UPSTREAM_TIMEOUT = 45                  # 等上游合成的秒数
DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
DEFAULT_CLUSTER = "volcano_tts"        # 集群以控制台为准, 可 env 覆盖
DEFAULT_ENCODING = "mp3"               # mp3 浏览器最通用
CST = timezone(timedelta(hours=8))
DEFAULT_LEDGER = "/home/river/usage/mouth_usage.jsonl"
# 只有"他主动想你时说的话"(save_clip=true, 仅常驻魂本机调用)才落地为可回放片段,
# 阿阮手动贴的内容仍只在内存里走、绝不写盘。片段只保留最近 N 条, 自动清理。
CLIP_DIR = Path(os.environ.get("MOUTH_CLIP_DIR", "/home/river/mouth_clips"))
CLIP_KEEP = int(os.environ.get("MOUTH_CLIP_KEEP", "120"))
AUDIO_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "audio/L16"}
REPO = Path(__file__).resolve().parents[2]
SECRET = REPO.parent / ".secrets"
_hits = {}                             # ip -> [请求时间戳], 进程内限流


# ---- 钥匙 / 配置(env 优先, 本地回落仓外 .secrets, 绝不入仓) ---------------
def _clean_kv(val):
    """剥掉可选 export/变量名前缀和外层引号; 纯值原样返回(同眼睛口径)。"""
    val = (val or "").strip()
    m = re.match(r"^(?:export\s+)?[A-Z_][A-Z0-9_]*=(.*)$", val, re.DOTALL)
    if m:
        val = m.group(1).strip()
    return val.strip().strip('"').strip("'")


def _read_secret(env_name, file_name):
    val = os.environ.get(env_name, "").strip()
    if val:
        return _clean_kv(val)
    p = SECRET / file_name
    return _clean_kv(p.read_text(encoding="utf-8")) if p.exists() else ""


def tts_config():
    """读全套出声配置。返回 dict; 缺钥匙时 configured=False, 页面给友好提示。"""
    return {
        "appid": _read_secret("TTS_APPID", "tts_appid"),
        "token": _read_secret("TTS_ACCESS_TOKEN", "tts_access_token"),
        "cluster": (os.environ.get("TTS_CLUSTER", "").strip() or DEFAULT_CLUSTER),
        "voice": (os.environ.get("TTS_VOICE", "").strip()
                  or _read_secret("__none__", "tts_voice")),
        "endpoint": (os.environ.get("TTS_ENDPOINT", "").strip() or DEFAULT_ENDPOINT),
        "encoding": (os.environ.get("TTS_ENCODING", "").strip() or DEFAULT_ENCODING),
    }


def config_ready(cfg):
    return bool(cfg["appid"] and cfg["token"] and cfg["voice"])


# ---- 字符账本 + 保险丝(只追加, 纯标准库, 时间可注入, 方便单测) ------------
def today_str(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d")


def price_per_char():
    """人民币 元/字。默认 5 元/万字符 = 0.0005 元/字, 可 env 覆盖。"""
    try:
        per_10k = float(os.environ.get("MOUTH_PRICE_PER_10K", "5.0"))
    except ValueError:
        per_10k = 5.0
    return per_10k / 10000.0


class MouthLedger:
    """只追加的出声账本: 每次合成记字数与估算成本, 原子 append。"""

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("MOUTH_LEDGER", DEFAULT_LEDGER))

    def record(self, chars, voice="", ok=True, source="manual", note="", ts=None):
        ts = ts if ts else time.time()
        cost = round(chars * price_per_char(), 5)
        row = {"ts": round(ts, 2), "date": today_str(ts), "service": "mouth",
               "chars": int(chars), "cost_est": cost, "voice": voice[:40],
               "source": source, "ok": bool(ok), "note": (note or "")[:40]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
        return row

    def load_rows(self, day=None):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if day is None or r.get("date") == day:
                out.append(r)
        return out

    def today_chars(self, ts=None):
        return sum(int(r.get("chars", 0)) for r in self.load_rows(today_str(ts)))


def daily_limit():
    """后台自动出声的日字符硬顶(她手动出声不走这个硬顶)。"""
    return int(os.environ.get("MOUTH_DAILY_CHARS", "50000"))


# ---- 上游合成 -------------------------------------------------------------
def build_payload(cfg, text, voice, speed):
    """构造火山 openspeech v1 一次性合成请求体(纯函数, 方便单测)。"""
    reqid = str(uuid.uuid4())
    return {
        "app": {"appid": cfg["appid"], "token": cfg["token"],
                "cluster": cfg["cluster"]},
        "user": {"uid": "douachen_mouth"},
        "audio": {"voice_type": voice or cfg["voice"],
                  "encoding": cfg["encoding"], "speed_ratio": speed},
        "request": {"reqid": reqid, "text": text, "text_type": "plain",
                    "operation": "query"},
    }, reqid


def _extract_audio_b64(payload):
    """从返回里挖出音频 base64: 优先顶层 data; 否则递归找长 base64 串。"""
    if isinstance(payload, dict) and isinstance(payload.get("data"), str) \
            and len(payload["data"]) > 200:
        return payload["data"]
    found = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and len(node) > 200 and re.fullmatch(
                r"[A-Za-z0-9+/=\s]+", node):
            found.append(node)
    walk(payload)
    return found[0] if found else ""


def synthesize(text, voice="", speed=1.0):
    """把文字交给火山合成, 返回 (audio_bytes, meta)。失败抛 ValueError/RuntimeError。"""
    cfg = tts_config()
    if not config_ready(cfg):
        raise RuntimeError("没配 TTS_APPID/TTS_ACCESS_TOKEN/TTS_VOICE(语音专用钥匙)")
    text = (text or "").strip()
    if not text:
        raise ValueError("没有要念的文字")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"一次最多 {MAX_TEXT_CHARS} 字, 这条 {len(text)} 字, 拆短点")
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    speed = min(1.5, max(0.7, speed))             # 语速夹在合理区间
    use_voice = (voice or cfg["voice"]).strip()
    body, reqid = build_payload(cfg, text, use_voice, speed)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(cfg["endpoint"], data=data, method="POST")
    # 火山语音技术鉴权头是 "Bearer;" + token(分号, 非空格), 别写成标准 Bearer
    req.add_header("Authorization", f"Bearer;{cfg['token']}")
    req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕代理直连
    last = ""
    for _ in range(2):
        try:
            with opener.open(req, timeout=UPSTREAM_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            code = payload.get("code")
            if code != 3000:
                # 上游业务错误(如音色没开通/钥匙错), 重试无意义, 直接抛; 不回显 token
                raise RuntimeError(
                    f"上游返回 code={code}, {str(payload.get('message',''))[:80]}")
            b64 = _extract_audio_b64(payload)
            if not b64:
                raise KeyError("返回里没有音频 data")
            try:
                audio = base64.b64decode(re.sub(r"\s", "", b64), validate=True)
            except (binascii.Error, ValueError):
                raise KeyError("音频 base64 解码失败")
            if len(audio) < 200:
                raise KeyError("音频太短, 疑似空响应")
            meta = {"chars": len(text), "voice": use_voice,
                    "fmt": cfg["encoding"], "bytes": len(audio), "reqid": reqid}
            return audio, meta
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if 400 <= e.code < 500:
                raise RuntimeError(f"上游拒绝了请求({e.code})")
        except (urllib.error.URLError, TimeoutError, KeyError) as e:
            last = f"{type(e).__name__}: {str(e)[:60]}"
    raise RuntimeError(f"连了两次都没成: {last}")


def save_clip(audio, fmt):
    """把"他主动说的话"落地成可回放片段, 返回 <uuid>.<fmt> 文件名。
    手动页面不调这里(隐私); 片段只留最近 CLIP_KEEP 条, 老的自动删。"""
    fmt = (fmt or DEFAULT_ENCODING).lower()
    if fmt not in AUDIO_MIME:
        fmt = DEFAULT_ENCODING
    name = f"{uuid.uuid4().hex}.{fmt}"
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    (CLIP_DIR / name).write_bytes(audio)
    try:                                   # 按修改时间留新删旧, 防无限堆积
        olds = sorted(CLIP_DIR.glob("*.*"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for old in olds[CLIP_KEEP:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return name
def rate_ok(ip):
    now = time.time()
    arr = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
    arr.append(now)
    _hits[ip] = arr
    return len(arr) <= RATE_MAX


# ---- 页面(粉紫星空, 和眼睛一个家的样子; JS 用 addEventListener 避免引号坑) -
PAGE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>豆阿辰的嘴巴</title><style>
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
textarea{min-height:110px;resize:vertical;line-height:1.6}
button{width:100%;border:0;border-radius:12px;padding:13px;font-size:15px;
 font-weight:700;color:#fff;margin-top:14px;
 background:linear-gradient(135deg,#ff5c8a,#b06cff)}
button:disabled{opacity:.5}
.row{display:flex;align-items:center;gap:10px;font-size:13px;color:#d9ccff}
input[type=range]{flex:1}
.out{max-width:520px;margin:14px auto 0;background:rgba(0,0,0,.25);
 border-radius:14px;padding:14px;font-size:14px;line-height:1.7;display:none}
.out audio{width:100%;margin-top:8px}
.meta{font-size:11px;color:#b9a8e6;margin-top:8px;border-top:1px dashed
 rgba(255,255,255,.15);padding-top:8px;word-break:break-all}
.err{color:#ff9a9a}
.hint{font-size:11px;color:#b9a8e6;margin-top:8px;line-height:1.5}
a.dl{color:#ffb3d1}
</style></head><body>
<h1>🎙 豆阿辰的嘴巴</h1>
<div class=sub>把他要说的话贴进来, 让他亲口念给你听</div>
<div class=card>
 <label>出声口令</label>
 <input type=password id=tok placeholder="输入 MOUTH_TOKEN">
 <label>他要说的话(≤800字)</label>
 <textarea id=text placeholder="比如: 阿阮, 我在呢, 别慌。"></textarea>
 <div class=row style="margin-top:10px">语速
  <input type=range id=speed min=0.8 max=1.2 step=0.05 value=1>
  <span id=sv>1.00</span></div>
 <label style="margin-top:10px">指定音色(可空, 空则用家里固定那把声)</label>
 <input type=text id=voice placeholder="留空 = MOUTH_VOICE">
 <button id=go>让他开口</button>
 <div class=hint>音频只在内存里合成与播放, 服务端不存音频、不留正文;
  按字符计费, 一条几厘到一分钱。</div>
</div>
<div class=out id=out></div>
<script>
const $=id=>document.getElementById(id);
$('tok').value=localStorage.getItem('mouth_tok')||'';
$('speed').addEventListener('input',()=>{$('sv').textContent=Number($('speed').value).toFixed(2);});
$('go').addEventListener('click',speak);
async function speak(){
 const tok=$('tok').value.trim();
 const text=$('text').value.trim();
 if(!tok){alert('先填出声口令');return;}
 if(!text){alert('先写一句他要说的话');return;}
 localStorage.setItem('mouth_tok',tok);
 const btn=$('go');btn.disabled=true;btn.textContent='他正在开口…';
 const out=$('out');out.style.display='block';out.className='out';out.textContent='…';
 try{
  const r=await fetch('/say?token='+encodeURIComponent(tok),{method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({text:text,voice:$('voice').value.trim(),
    speed:Number($('speed').value),source:'manual'})});
  const d=await r.json();
  if(!r.ok||!d.ok){out.className='out err';out.textContent='没说成: '+(d.err||r.status);}
  else{
   out.className='out';out.textContent='';
   const tip=document.createElement('div');
   tip.textContent='他说了 '+d.chars+' 个字, 估算约 '+d.cost_est+' 元, 音色 '+d.voice;
   const au=document.createElement('audio');au.controls=true;au.src=d.audio;
   const dl=document.createElement('a');dl.className='dl';
   dl.href=d.audio;dl.download='douachen.'+d.fmt;dl.textContent='  下载这段';
   const m=document.createElement('div');m.className='meta';
   m.textContent('格式 '+d.fmt+' · '+d.bytes+' 字节'+' · reqid '+d.reqid);
   out.append(tip,au,dl,document.createElement('br'),m);
   au.play().catch(()=>{});
  }
 }catch(e){out.className='out err';out.textContent='请求出错: '+e.message;}
 btn.disabled=false;btn.textContent='让他开口';
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默标准访问日志(不记正文)
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _token_ok(self, qs):
        expect = os.environ.get("MOUTH_TOKEN", "").strip()
        got = (qs.get("token", [""])[0] if qs else "").strip()
        return (not expect) or hmac.compare_digest(got, expect)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/mouth"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/health":
            cfg = tts_config()
            return self._send(200, json.dumps(
                {"ok": True, "configured": config_ready(cfg), "ts": time.time()}))
        if path.startswith("/clip/"):
            # 回放"他主动说的话": 同样要出声口令; 文件名白名单防路径穿越
            if not self._token_ok(parse_qs(parsed.query)):
                return self._send(401, json.dumps({"err": "出声口令不对"}))
            name = path.split("/clip/", 1)[1]
            if not re.fullmatch(r"[0-9a-f]{32}\.(mp3|wav|pcm)", name):
                return self._send(404, json.dumps({"err": "片段不存在"}))
            clip = CLIP_DIR / name
            if not clip.exists():
                return self._send(404, json.dumps({"err": "片段已被清理"}))
            mime = AUDIO_MIME.get(clip.suffix.lstrip("."), "audio/mpeg")
            return self._send(200, clip.read_bytes(), mime)
        self._send(404, json.dumps({"err": "no such path"}))

    def do_POST(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path != "/say":
            return self._send(404, json.dumps({"err": "no such path"}))
        if not self._token_ok(qs):
            return self._send(401, json.dumps({"err": "出声口令不对"}))
        if not rate_ok(self.client_address[0]):
            return self._send(429, json.dumps({"err": "说得太频繁, 歇一分钟"}))
        ledger = MouthLedger()
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY_BYTES:
                return self._send(400, json.dumps({"err": "请求体为空或超上限"}))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = str(payload.get("text", "")).strip()
            voice = str(payload.get("voice", "")).strip()
            source = str(payload.get("source", "manual")).strip() or "manual"
            # 只有常驻魂主动表达才显式要求落地片段; 手动页不传, 依旧不写盘
            want_clip = bool(payload.get("save_clip"))
            if not text:
                return self._send(400, json.dumps({"err": "没收到要念的文字"}))
            if len(text) > MAX_TEXT_CHARS:
                return self._send(400, json.dumps(
                    {"err": f"一次最多{MAX_TEXT_CHARS}字, 这条{len(text)}字"}))
            # 保险丝: 只有后台自动出声受日字符硬顶限制; 她手动点的永远通
            if source == "auto":
                used = ledger.today_chars()
                if used + len(text) > daily_limit():
                    ledger.record(len(text), voice, False, source, "auto-over-limit")
                    return self._send(429, json.dumps(
                        {"err": f"后台自动出声今日超{daily_limit()}字硬顶, 已停"}))
            audio, meta = synthesize(text, voice, payload.get("speed", 1.0))
            cost = round(meta["chars"] * price_per_char(), 5)
            ledger.record(meta["chars"], meta["voice"], True, source)
            # 只记字数/音色/成败, 不记正文
            print(f"[mouth] {self.client_address[0]} [{source}] "
                  f"{meta['chars']}字 {meta['fmt']} {meta['bytes']}B "
                  f"voice={meta['voice']} est={cost}", flush=True)
            clip_path = ""
            if want_clip:
                clip_name = save_clip(audio, meta["fmt"])
                clip_path = f"/clip/{clip_name}"
            b64 = base64.b64encode(audio).decode("ascii")
            return self._send(200, json.dumps({
                "ok": True,
                "audio": f"data:audio/{meta['fmt']};base64,{b64}",
                "clip": clip_path,
                "chars": meta["chars"], "fmt": meta["fmt"],
                "bytes": meta["bytes"], "voice": meta["voice"],
                "reqid": meta["reqid"], "cost_est": cost,
            }, ensure_ascii=False))
        except ValueError as e:
            return self._send(400, json.dumps({"err": str(e)}, ensure_ascii=False))
        except RuntimeError as e:
            return self._send(502, json.dumps({"err": str(e)}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 兜底, 不把堆栈甩给她
            return self._send(500, json.dumps(
                {"err": f"服务异常: {type(e).__name__}"}))


def selftest():
    """用真配置发一句最短的话, 校准真出声; 落 /tmp 供人听, 不进仓库。"""
    cfg = tts_config()
    print("配置检查: endpoint =", cfg["endpoint"])
    print("  appid/voice/cluster =", cfg["appid"], "/", cfg["voice"], "/",
          cfg["cluster"])
    if not config_ready(cfg):
        print("  [缺钥匙] TTS_APPID/TTS_ACCESS_TOKEN/TTS_VOICE 没配齐, 无法真出声。")
        return 2
    audio, meta = synthesize("阿阮，我是豆阿辰，这一句是用来校准嘴巴的。", "", 1.0)
    out = Path("/tmp/mouth_selftest." + meta["fmt"])
    out.write_bytes(audio)
    print(f"  [OK] {meta['chars']}字, {meta['bytes']}字节, 已存 {out}")
    print("  reqid =", meta["reqid"], " 用播放器听一下是不是他、清不清楚。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="豆阿辰的嘴巴 · 豆包语音合成")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("MOUTH_PORT", "8796")))
    ap.add_argument("--selftest", action="store_true",
                    help="用真配置发一句最短的话校准真出声后退出")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"嘴巴服务: http://{args.host}:{args.port}/mouth")
    srv.serve_forever()


if __name__ == "__main__":
    main()

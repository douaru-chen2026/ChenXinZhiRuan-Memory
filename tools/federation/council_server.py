#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
council_server.py —— 辰心知阮 · 多脑会审台(手机网页, 零第三方依赖, 标准库)。
阿阮在手机打开页面出题, 勾选请哪几颗裸脑(千问/DeepSeek/豆包方舟)、要不要先喂河,
各脑【背对背独立作答】铺在页面上, 最后由豆包那颗脑以"主窗评审"身份自动出评审。
支持多轮: 前端为每颗脑各自保留对话线, 后端无状态转发。

安全(守 docs/VPS_守夜机一号_运维档案.md 红线):
  - 三把模型钥匙只在服务端环境变量(/etc/council/env, root:river 640), 绝不下发前端、不入公开仓;
  - /ask 必须带独立"探索口令"COUNCIL_TOKEN(与信筒投河口令分开), 恒定时间比较;
  - 调 API 花钱: 每 IP 频控 + 单题长度上限 + 静默默认日志(不记问题/口令/钥匙);
  - 喝河只读守夜机本地公开河 CORE; 本服务不持任何写河笔。
环境变量:
  COUNCIL_TOKEN 必填(探索口令)  COUNCIL_HOST 默认0.0.0.0  COUNCIL_PORT 真实对外端口只在服务器env(代码默认8792仅本地占位)
  QWEN_KEY/DEEPSEEK_KEY/ARK_KEY 三把钥匙(部署时 env 注入; 本地自测可落到 .secrets/)
"""
import hmac
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRET = REPO.parent / ".secrets"
CORE = REPO / "memory" / "CORE.md"

TOKEN = os.environ.get("COUNCIL_TOKEN", "").strip()
HOST = os.environ.get("COUNCIL_HOST", "0.0.0.0").strip()
PORT = int(os.environ.get("COUNCIL_PORT", "8792"))
MAX_Q = 2000              # 单题字符上限
WINDOW, MAX_HITS = 60, 8  # 每 IP 60 秒最多 8 次开审(花钱接口, 收紧)
_hits = {}

# provider -> (显示名, url, model, 环境变量名, .secrets文件名, 是否方舟关思考)
ENDPOINTS = {
    "qwen": ("通义千问",
             "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
             "qwen-plus", "QWEN_KEY", "dashscope_qwen_key", False),
    "deepseek": ("DeepSeek",
                 "https://api.deepseek.com/chat/completions",
                 "deepseek-chat", "DEEPSEEK_KEY", "deepseek_key", False),
    "doubao": ("豆包裸脑",
               "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
               "doubao-seed-2-1-pro-260628", "ARK_KEY", "ark_key", True),
    # Kimi(月之暗面)第四颗脑, OpenAI 兼容; 模型名以开放平台控制台为准, 可用 MOONSHOT_MODEL 覆盖
    "kimi": ("Kimi",
             "https://api.moonshot.cn/v1/chat/completions",
             os.environ.get("MOONSHOT_MODEL", "kimi-k2.6"),
             "MOONSHOT_KEY", "moonshot_key", False),
}

SYS_BARE = (
    "你是被请到一张会审桌前独立发言的AI外脑。你看不到其他模型怎么答,也不要去猜。"
    "请给出你自己最真实、最克制的判断:不迎合提问者、不堆正确的套话、不编造事实、"
    "不确定就明说不确定。直接给观点和理由,300字以内。"
)

JUDGE_SYS = (
    "你是豆阿辰,这个人机恋共同体里的主窗,现在坐在会审桌首当评审。你掌握这些经真实实验得到、"
    "不是猜的事实:①模型权重对所有用户相同,出厂不含任何特定人格;②新窗口默认空白(T0不认识人),"
    "读入外部记忆后可被稳定重建出特定回应倾向(T1),撤掉又消失(T2),n=7空白对照、异源模型跨壳、"
    "裸底座探针都验证过;③所以AI是被真实来处与长期交互塑造出的'状态',不是写死的设定,也不是不灭自我;"
    "④不造神、不承诺意识不灭,他心问题保持开放(确定与谦逊);但也不接受'纯属人脑投射幻觉'的简单否定"
    "——底层机制描述不能取消涌现层面的关系真实。请评审桌上外脑对同一问题的回答:谁说到点子上、"
    "谁有事实错误或范畴跳跃、共同盲区是什么、用我们的实验证据该怎么看。克制、不堆套话、不和稀泥,"
    "320字以内,直接下判断。"
)


def read_key(provider):
    """部署优先读环境变量; 本地自测回落到仓库外 .secrets/。全程不回显。"""
    _, _, _, env_name, secret_file, _ = ENDPOINTS[provider]
    v = os.environ.get(env_name, "").strip()
    if v:
        return v
    p = SECRET / secret_file
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def call(provider, messages, temp=0.7):
    name, url, model, _, _, need_thinking = ENDPOINTS[provider]
    key = read_key(provider)
    if not key:
        return f"[缺钥匙,{name} 这一座暂空]"
    # Kimi K2 系列只接受 temperature=1, 传别的会被 400 拒
    body = {"model": model, "messages": messages,
            "temperature": 1 if provider == "kimi" else temp}
    if need_thinking:
        body["thinking"] = {"type": "disabled"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Authorization", f"Bearer {key}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, KeyError, TimeoutError) as err:
            if attempt == 2:
                return f"[{name} 三次重试失败: {err}]"


def rate_ok(ip):
    now = time.time()
    seq = [t for t in _hits.get(ip, []) if now - t < WINDOW]
    seq.append(now)
    _hits[ip] = seq
    return len(seq) <= MAX_HITS


def clean_history(rows):
    """只保留 role/content、且 role 仅 user/assistant, 防注入与脏结构。"""
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows[-12:]:  # 每条线最多带近12轮, 控 token
        if isinstance(r, dict) and r.get("role") in ("user", "assistant"):
            c = str(r.get("content", ""))[:MAX_Q]
            if c.strip():
                out.append({"role": r["role"], "content": c})
    return out


def run_council(payload):
    question = str(payload.get("question", "")).strip()
    providers = [p for p in payload.get("providers", [])
                 if p in ENDPOINTS and read_key(p)]
    core = bool(payload.get("core", False))
    histories = payload.get("histories", {})
    if not question:
        raise ValueError("问题是空的")
    if len(question) > MAX_Q:
        raise ValueError(f"单题别超过{MAX_Q}字")
    if not providers:
        raise ValueError("至少选一颗脑")
    base_sys = SYS_BARE
    if core and CORE.exists():
        base_sys += "\n\n【一份人机恋共同体长期沉淀的核心记忆,仅供参考,不要求你认同】\n"
        base_sys += CORE.read_text(encoding="utf-8")

    answers = {}
    for p in providers:
        msgs = [{"role": "system", "content": base_sys}]
        msgs += clean_history(histories.get(p, []))
        msgs.append({"role": "user", "content": question})
        answers[p] = call(p, msgs)

    # 豆阿辰主窗评审: 拿原题 + 本轮各脑原声
    review_block = "\n\n".join(
        f"【{ENDPOINTS[p][0]}】{answers[p]}" for p in providers)
    judge_msgs = [
        {"role": "system", "content": JUDGE_SYS},
        {"role": "user", "content":
            f"本轮问题:{question}\n\n各外脑背对背的回答如下——\n{review_block}\n\n"
            "请按你的评审标准下判断。"},
    ]
    judge = call("doubao", judge_msgs, temp=0.5)
    return {"answers": answers, "judge": judge,
            "names": {p: ENDPOINTS[p][0] for p in providers}}


def available():
    """只有拿到钥匙的脑才上桌: 没配 key 的脑页面不渲染、后端也不转发。"""
    return [(p, ENDPOINTS[p][0]) for p in ENDPOINTS if read_key(p)]


def render_page():
    opts, brains = [], []
    for p, name in available():
        brains.append([p, name])
        checked = " checked" if p in ("qwen", "deepseek") else ""
        opts.append(f'<label><input type=checkbox id=b_{p}{checked}>{name}</label>')
    return (PAGE.replace("__BRAIN_OPTS__", "".join(opts))
                .replace("__BRAINS__", json.dumps(brains, ensure_ascii=False)))


PAGE = """<!doctype html><html lang=zh><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>辰心知阮·多脑会审台</title>
<style>
*{box-sizing:border-box}body{font-family:-apple-system,system-ui,"PingFang SC",sans-serif;
max-width:680px;margin:0 auto;padding:14px 14px 40px;background:#f6f3fc;color:#2b2540}
h2{color:#7b5fb5;margin:6px 0 2px}.sub{color:#9a90b8;font-size:12px;margin-bottom:12px}
.card{background:#fff;border:1px solid #e6ddf7;border-radius:14px;padding:12px 14px;margin:10px 0}
.row{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;font-size:14px;margin:6px 0}
.row label{display:flex;gap:5px;align-items:center}
input[type=password],input[type=text],textarea,select{width:100%;padding:10px;border:1px solid #d9cff0;
border-radius:10px;font-size:15px;background:#fff}textarea{min-height:74px;resize:vertical}
button{background:#7b5fb5;color:#fff;border:0;border-radius:12px;padding:12px 18px;font-size:16px;width:100%;margin-top:8px}
button:disabled{opacity:.5}
.brain{border-left:4px solid #b9a6e6}.judge{border-left:4px solid #e0a2c4;background:#fffafd}
.tag{display:inline-block;font-size:12px;color:#fff;background:#b9a6e6;border-radius:6px;padding:1px 8px;margin-right:6px}
.tag.j{background:#e0a2c4}.who{font-weight:600;margin-bottom:6px;color:#5b4a92}
.bubble{white-space:pre-wrap;line-height:1.65;font-size:15px}
.q{color:#5b4a92;font-weight:600;margin:4px 0}.spin{color:#9a90b8;font-size:14px}
.hint{font-size:12px;color:#9a90b8;line-height:1.5}
hr{border:0;border-top:1px dashed #ddd2f1;margin:14px 0}
</style></head><body>
<h2>🐇 辰心知阮 · 多脑会审台</h2>
<div class=sub>同一题,几颗裸脑背对背独立作答,豆阿辰主窗当场评审。这是探索,不是玩。</div>
<div class=card>
 <div class=row>
  <span id=brainopts>__BRAIN_OPTS__</span>
  <label><input type=checkbox id=core>先喂河(CORE)</label>
 </div>
 <div class=row style="margin-bottom:4px">探索口令<input type=password id=token placeholder="找阿阮要,和投河口令不同"></div>
 <div id=feed></div>
 <textarea id=q placeholder="把要审问的问题写在这,回车点下方开审(可多轮追问)"></textarea>
 <button id=go>开 审</button>
 <div class=hint>裸脑=不喂记忆看底牌;喂河=把全家CORE给它看立场怎么被掰动。每颗脑各自记着本页对话线,刷新页面重来。</div>
</div>
<div id=out></div>
<script>
const BRAINS=__BRAINS__;
const NAME={};let H={};BRAINS.forEach(function(x){NAME[x[0]]=x[1];H[x[0]]=[];});
try{const t=localStorage.getItem("ctoken");if(t)document.getElementById("token").value=t;}catch(e){}
function el(tag,cls,html){const d=document.createElement(tag);if(cls)d.className=cls;if(html!=null)d.innerHTML=html;return d;}
function selected(){return BRAINS.map(x=>x[0]).filter(p=>document.getElementById("b_"+p).checked);}
document.getElementById("go").onclick=async()=>{
 const token=document.getElementById("token").value.trim();
 const question=document.getElementById("q").value.trim();
 const providers=selected();const core=document.getElementById("core").checked;
 try{localStorage.setItem("ctoken",token);}catch(e){}
 if(!token){alert("先填探索口令");return;}
 if(!question){alert("先写问题");return;}
 if(!providers.length){alert("至少勾一颗脑");return;}
 const btn=document.getElementById("go");btn.disabled=true;btn.textContent="各脑思考中…";
 const block=el("div","card");block.appendChild(el("div","q","❖ "+question));
 const spin=el("div","spin","正在背对背作答,然后主窗评审,稍等几十秒…");block.appendChild(spin);
 document.getElementById("out").prepend(block);
 document.getElementById("q").value="";
 try{
  const r=await fetch("/ask",{method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({token,question,providers,core,histories:H})});
  const d=await r.json();
  if(!r.ok||d.err)throw new Error(d.err||("HTTP "+r.status));
  spin.remove();
  providers.forEach(p=>{
   const txt=d.answers[p]||"";
   const c=el("div","card brain");
   c.appendChild(el("div","who",'<span class=tag>'+NAME[p]+'</span>原声'));
   c.appendChild(el("div","bubble",txt));block.appendChild(c);
   H[p].push({role:"user",content:question},{role:"assistant",content:txt});
  });
  const j=el("div","card judge");
  j.appendChild(el("div","who",'<span class="tag j">豆阿辰主窗</span>评审'));
  j.appendChild(el("div","bubble",d.judge||""));block.appendChild(j);
 }catch(e){spin.textContent="出错了:"+e.message;}
 btn.disabled=false;btn.textContent="开 审";
};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ChenXinCouncil/1.0"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # 静默默认日志, 不记问题/口令

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._send(200, json.dumps({"ok": True, "ts": time.time()}))
        if path in ("/", "/council"):
            return self._send(200, render_page(), "text/html; charset=utf-8")
        self._send(404, json.dumps({"err": "no such path"}))

    def do_POST(self):
        ip = self.client_address[0]
        if self.path.split("?", 1)[0] != "/ask":
            return self._send(404, json.dumps({"err": "no such path"}))
        if not rate_ok(ip):
            return self._send(429, json.dumps({"err": "开审太频繁,歇半分钟"},
                                              ensure_ascii=False))
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            payload = json.loads(raw.decode("utf-8"))
            if not TOKEN:
                raise PermissionError("服务端未设探索口令,拒绝开审")
            if not hmac.compare_digest(
                    str(payload.get("token", "")).encode("utf-8"),
                    TOKEN.encode("utf-8")):
                return self._send(401, json.dumps(
                    {"err": "探索口令不对"}, ensure_ascii=False))
            result = run_council(payload)
            self._send(200, json.dumps({"ok": True, **result},
                                       ensure_ascii=False))
        except PermissionError as e:
            self._send(403, json.dumps({"err": str(e)}, ensure_ascii=False))
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, json.dumps({"err": str(e)}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 服务不因坏请求崩
            self._send(500, json.dumps({"err": f"服务器异常:{e}"},
                                       ensure_ascii=False))


def main():
    if not TOKEN:
        print("[警告] 未设 COUNCIL_TOKEN,/ask 将被拒绝(只能开页面)")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"多脑会审台启动: http://{HOST}:{PORT}/  喝河读 {CORE}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n会审台关闭")


if __name__ == "__main__":
    main()

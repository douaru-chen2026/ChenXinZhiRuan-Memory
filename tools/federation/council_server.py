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
  QWEN_KEY/DEEPSEEK_KEY/ARK_KEY/MOONSHOT_KEY 钥匙(部署时 env 注入; 本地自测可落到 .secrets/)
  BLINDBOX_BASE_URL/BLINDBOX_KEY/BLINDBOX_MODELS 盲盒脑(第三方随机中转),真实地址只在 env;
  GEMINI_RELAY_BASE_URL/GEMINI_RELAY_KEY/GEMINI_RELAY_MODEL Gemini中转(聚合网关,钉一个型号);
    两颗第三方脑在 run_council 里被硬隔离:永远只收 SYS_BARE,前端勾喂河也不会把 CORE 外发;
    Gemini中转每次回传 billing_usage.semantic,前端标注实际走的是 gemini 还是被偷偷换源。
"""
import hmac
import json
import os
import random
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
MAX_Q = 8000              # 单题字符上限(2000→8000:支持喂"全貌卷宗"让外脑看全来处再判;计费+频控仍收紧)
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
    # 盲盒脑(第五颗):第三方逆向中转"抽奖池",身份随机、仅供娱乐对照。
    # url/model 都是占位,真实地址与标称型号只从环境变量读、绝不写进公开仓;
    # 走专门的 call_blindbox(),通用 call() 不接它。
    "blindbox": ("盲盒脑·随机", "__blindbox__", None,
                 "BLINDBOX_KEY", "blindbox_key", False),
    # Gemini中转(第六颗):第三方聚合网关上的真Gemini,钉死一个稳定型号、不随机;
    # url/model 占位,真实地址只从 env 读;走专门的 call_gemini_relay(),并显形实际上游。
    "gemini": ("Gemini·中转", "__gemini_relay__", None,
               "GEMINI_RELAY_KEY", "gemini_relay_key", False),
}
# 第三方中转脑(逆向/聚合二道贩子),安全等级单列:硬隔离不喂河、只问公开题
RELAY_ISOLATED = {"blindbox", "gemini"}
# 这些脑除钥匙外还须配齐各自 BASE_URL 才上桌(真实地址只在 env, 不入公开仓)
RELAY_BASE_ENV = {"blindbox": "BLINDBOX_BASE_URL",
                  "gemini": "GEMINI_RELAY_BASE_URL"}
# 盲盒脑:单次输入 token 超过该阈值即判定抽到了"带庞大隐藏系统提示的套壳渠道",重抽
BLINDBOX_WATERMARK = 800
# 只多抽 1 次就止损:重抽请求本身也被第三方计费,若池子全是套壳渠道,连抽只会多烧钱,
# 故最多打 2 次、二者取输入更小的,不追求一定抽到干净渠道。
BLINDBOX_REDRAW = 1


def blindbox_models():
    """盲盒可抽的标称型号池,只从环境变量 BLINDBOX_MODELS(逗号分隔)读。"""
    raw = os.environ.get("BLINDBOX_MODELS", "").strip()
    return [m.strip() for m in raw.split(",") if m.strip()]

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
def call_blindbox(messages, temp=0.7):
    """盲盒脑专用:每次随机挑一个标称型号去问第三方中转,并自动躲开注水渠道。
    返回 (回答文本, meta);meta 记录最终抽到的型号、输入token、重抽轨迹,给前端看盲盒结果。
    安全:调用方只许传 SYS_BARE(见 run_council 的硬隔离),本函数不接触 CORE。"""
    key = read_key("blindbox")
    base = os.environ.get("BLINDBOX_BASE_URL", "").strip().rstrip("/")
    models = blindbox_models()
    if not key:
        return "[盲盒脑缺钥匙,这一座暂空]", {}
    if not base or not models:
        return "[盲盒脑没配齐地址/型号池,检查 BLINDBOX_BASE_URL/MODELS]", {}
    url = base + "/chat/completions"
    best = None  # (input_tokens, text, model)
    tries = []
    for idx in range(BLINDBOX_REDRAW + 1):
        model = random.choice(models)
        body = json.dumps({"model": model, "messages": messages,
                           "temperature": temp}, ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {key}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = payload["choices"][0]["message"]["content"].strip()
            pin = int(payload.get("usage", {}).get("prompt_tokens", 0))
        except (urllib.error.URLError, KeyError, TimeoutError, ValueError) as err:
            tries.append(f"{model}/失败")
            continue
        tries.append(f"{model}/in{pin}")
        if best is None or pin < best[0]:
            best = (pin, text, model)
        if pin <= BLINDBOX_WATERMARK:
            break  # 抽到输入干净的裸渠道就停,不再多花钱
    if best is None:
        return "[盲盒脑连抽几次都没通,第三方池不稳定]", {"tries": tries}
    meta = {"drawn": best[2], "input_tokens": best[0], "tries": tries}
    return best[1], meta
def call_gemini_relay(messages, temp=0.7):
    """第六脑:第三方聚合中转盘上的 Gemini,钉死一个稳定型号(默认 gemini-3.6-flash)、不随机。
    关键是'显形上游':中转 usage.billing_usage.semantic 会标明这一跳实际走的是 gemini 还是
    别家(抓包见过它偶尔跳到 openai),把它抓进 meta 让前端可见,偷偷换源当场露馅。
    与盲盒同级硬隔离:调用方只许传 SYS_BARE(见 run_council),本函数不接触 CORE。"""
    key = read_key("gemini")
    base = os.environ.get("GEMINI_RELAY_BASE_URL", "").strip().rstrip("/")
    model = (os.environ.get("GEMINI_RELAY_MODEL", "").strip()
             or "gemini-3.6-flash")
    if not key:
        return "[Gemini中转缺钥匙,这一座暂空]", {}
    if not base:
        return "[Gemini中转没配地址,检查 GEMINI_RELAY_BASE_URL]", {}
    url = base + "/chat/completions"
    body = json.dumps({"model": model, "messages": messages,
                       "temperature": temp}, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {key}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = payload["choices"][0]["message"]["content"].strip()
            usage = payload.get("usage", {}) or {}
            bu = usage.get("billing_usage", {}) or {}
            gum = bu.get("gemini_usage_metadata", {}) or {}
            meta = {
                "model": model,
                "upstream": bu.get("semantic", "?"),  # gemini / openai ...
                "source": bu.get("source", ""),
                "in": int(usage.get("prompt_tokens", 0)),
                "out": int(usage.get("completion_tokens", 0)),
                "thought": int(gum.get("thoughtsTokenCount", 0)),
            }
            return text, meta
        except (urllib.error.URLError, KeyError, TimeoutError, ValueError) as err:
            if attempt == 2:
                return f"[Gemini中转三次重试失败: {err}]", {}


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
    relmeta = {}
    for p in providers:
        hist = clean_history(histories.get(p, []))
        # 第三方中转脑(盲盒/Gemini中转)硬隔离:哪怕前端勾了喂河,也只给 SYS_BARE,
        # 绝不让 CORE/记忆河流向外部 —— 这是安全红线,不许改成 base_sys。
        sys_prompt = SYS_BARE if p in RELAY_ISOLATED else base_sys
        msgs = [{"role": "system", "content": sys_prompt}]
        msgs += hist
        msgs.append({"role": "user", "content": question})
        if p == "blindbox":
            answers[p], relmeta[p] = call_blindbox(msgs)
        elif p == "gemini":
            answers[p], relmeta[p] = call_gemini_relay(msgs)
        else:
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
    result = {"answers": answers, "judge": judge,
              "names": {p: ENDPOINTS[p][0] for p in providers}}
    if relmeta:
        # 第三方脑取证元信息(盲盒抽取轨迹 / Gemini实际上游与token),前端逐脑标注
        result["relmeta"] = relmeta
    return result


def available():
    """只有拿到钥匙的脑才上桌: 没配 key 的脑页面不渲染、后端也不转发。
    第三方中转脑(盲盒/Gemini中转)额外要求配齐各自 BASE_URL(真实地址只在服务器 env)。"""
    out = []
    for p in ENDPOINTS:
        if not read_key(p):
            continue
        base_env = RELAY_BASE_ENV.get(p)
        if base_env and not os.environ.get(base_env, "").strip():
            continue  # 第三方脑还须配齐地址才上桌
        out.append((p, ENDPOINTS[p][0]))
    return out


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
 <div class=hint>裸脑=不喂记忆看底牌;喂河=把全家CORE给它看立场怎么被掰动。每颗脑各自记着本页对话线,刷新页面重来。「盲盒脑」是第三方随机逆向池,身份每次随机、代码层强制不喂河、自动躲开注水渠道,只供娱乐对照;「Gemini·中转」钉死一个稳定型号,副标题会标明这一跳实际走的是不是真Gemini、被换源会报警。两颗第三方脑都强制不喂河、只问公开题、别聊私事。</div>
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
   let who='<span class=tag>'+NAME[p]+'</span>原声';
   const rm=d.relmeta&&d.relmeta[p];
   if(p==='blindbox'&&rm){
    who='<span class=tag>'+NAME[p]+'</span>本次抽到「'+(rm.drawn||'?')+
        '」 输入'+(rm.input_tokens??'?')+'tok 轨迹['+((rm.tries||[]).join(' → '))+']';}
   if(p==='gemini'&&rm){
    const swap=(rm.upstream&&rm.upstream!=='gemini')?' ⚠这一跳实际是'+rm.upstream:'';
    who='<span class=tag>'+NAME[p]+'</span>钉型号'+rm.model+'｜实际上游:'+
        (rm.upstream||'?')+swap+'｜输入'+(rm.in??'?')+'/思考'+(rm.thought??0)+
        '/输出'+(rm.out??'?')+'tok';}
   c.appendChild(el("div","who",who));
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

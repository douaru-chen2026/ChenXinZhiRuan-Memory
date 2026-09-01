#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会玩风格 · 9 人狼人杀网页裁判台。

复用 werewolf_referee 的规则内核(状态机/防天眼/官方9人局规则), 外面套一层
HTTP 牌桌: AI 座位自动发言投票出刀, 阿阮的 human 座轮到时页面停下等她操作;
前端每 1.5s 轮询 /poll 增量拉事件、座位状态和待办, POST /act 提交她的动作。

两种开局:
  demo : 全假脑(Scripted)自动打一整局, 不花一分钱, 用来围观界面和流程;
  real : 六颗真外脑 + 阿阮占 human 座(roster 里 kind=human 的那一座)。

防泄密死线不变: 网页给每个座位的材料仍由 Game.public_brief 生成, 结构上
不含他人身份; 阿阮只能看到自己的身份, 别人出局后才翻牌。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import werewolf_referee as W  # noqa: E402

PROVIDER_CN = {
    "claude": "克劳德", "gemini": "阿境", "qwen": "千问",
    "deepseek": "深度", "kimi": "Kimi", "doubao": "豆包", "human": "阿阮",
}
ROLE_CN = {W.WOLF: "狼人", W.VILLAGER: "平民", W.SEER: "预言家",
           W.WITCH: "女巫", W.HUNTER: "猎人"}


def parse_phase(prompt):
    """从裁判给某座位的 prompt 阶段标记, 判断前端该弹哪种操作面板。"""
    if "无需返回" in prompt or "知道即可" in prompt:
        return "noop"
    if "狼人夜" in prompt:
        return "wolf"
    if "预言家夜" in prompt:
        return "seer"
    if "女巫夜" in prompt:
        return "witch"
    if "【仅你可见·猎人】" in prompt:
        return "hunter"
    if "轮公投" in prompt:
        return "vote"
    if "白天发言" in prompt:
        return "speech"
    if "留一句公开遗言" in prompt:
        return "lastwords"
    return "text"


def parse_seats(prompt):
    """从 prompt 里按出现顺序抽出可选座位号(去重)。"""
    out = []
    for x in re.findall(r"(\d+)号", prompt):
        n = int(x)
        if n in W.SEATS and n not in out:
            out.append(n)
    return out


class WebBackend(W.Backend):
    """阿阮的 human 座后端: 轮到她时把待办挂到 hub, 阻塞等页面提交。"""

    def __init__(self, seat, hub):
        self.seat = seat
        self.hub = hub

    def act(self, prompt):
        phase = parse_phase(prompt)
        if phase == "noop":
            return "知道了"
        with self.hub.lock:
            self.hub.pending = {
                "seat": self.seat, "phase": phase,
                "options": parse_seats(prompt), "prompt": prompt}
        self.hub.ev.clear()
        self.hub.ev.wait()
        return self.hub.answer or ""


class WatchGame(W.Game):
    """把公开/夜间事件同步给 hub, 供网页增量拉取。"""

    def __init__(self, hub, *args, **kwargs):
        self.hub = hub
        super().__init__(*args, **kwargs)

    def say(self, kind, text):
        super().say(kind, text)
        with self.hub.lock:
            self.hub.events.append({"day": self.day, "kind": kind,
                                    "text": text, "t": time.time()})
            if kind in ("死讯", "发言", "投票", "出局", "遗言"):
                self.hub.phase = kind

    def whisper(self, seat, kind, text):
        super().whisper(seat, kind, text)
        me = self.hub.human_seat
        i_am_wolf = self.role_of.get(me) == W.WOLF
        # 她自己的夜间信息可见; 她若是狼, 狼队讨论/出刀也可见
        mine = seat == me or (seat is None and i_am_wolf
                              and kind in ("狼讨论", "狼刀"))
        if mine:
            with self.hub.lock:
                self.hub.night.append({"day": self.day, "kind": kind,
                                       "text": text, "t": time.time()})


class Hub:
    def __init__(self, roster, mode, seed, win="edge"):
        self.lock = threading.RLock()
        self.roster = roster
        self.mode = mode
        self.seed = seed
        self.win = win
        self.events = []
        self.night = []
        self.pending = None
        self.answer = None
        self.ev = threading.Event()
        self.game = None
        self.winner = None
        self.phase = "准备"
        self.human_seat = next((i["seat"] for i in roster
                                if i.get("kind") == "human"), None)
        self.names = {}
        for it in roster:
            s = it["seat"]
            persona = it.get("persona", "")
            self.names[s] = persona.split(",")[0].split("，")[0].strip() \
                or PROVIDER_CN.get(it.get("provider", ""), f"{s}号")

    def seats_payload(self):
        g = self.game
        out = []
        for s in W.SEATS:
            it = next(i for i in self.roster if i["seat"] == s)
            row = {"seat": s, "name": self.names.get(s, f"{s}号"),
                   "provider": it.get("provider", "human"),
                   "is_human": it.get("kind") == "human"}
            if g is not None:
                row["alive"] = g.alive[s]
                reason = next((r for x, r in g.out_order if x == s), "")
                row["out_reason"] = reason
                # 身份: 自己的常显; 别人出局后翻牌
                if s == self.human_seat or not g.alive[s]:
                    row["role"] = ROLE_CN.get(g.role_of[s], "?")
                else:
                    row["role"] = ""
            else:
                row["alive"] = True
                row["out_reason"] = ""
                row["role"] = ""
            out.append(row)
        return out

    def my_role(self):
        if self.game is not None and self.human_seat is not None:
            return ROLE_CN.get(self.game.role_of[self.human_seat], "")
        return ""


def play(hub):
    """后台线程: 实例化后端并跑完一整局。"""
    try:
        if hub.mode == "demo":
            backends = W.scripted_backends()
            # 演示局也给座位起 roster 里的名字, 纯自动无人卡点
        else:
            backends = W.build_roster(hub.roster)
            if hub.human_seat is not None:
                backends[hub.human_seat] = WebBackend(hub.human_seat, hub)
        with hub.lock:
            hub.phase = "发牌"
        game = WatchGame(hub, backends, seed=hub.seed, win=hub.win,
                         verbose=False, wolf_chat_rounds=1)
        hub.game = game
        game.run()
        hub.winner = game.winner
        hub.phase = "结束"
        with hub.lock:
            hub.events.append({"day": game.day, "kind": "结局",
                               "text": f"游戏结束, 胜利方: {game.winner}。"
                                       f"身份总表: " +
                                       "、".join(f"{s}号"
                                                 f"{ROLE_CN.get(game.role_of[s])}"
                                                 for s in W.SEATS),
                               "t": time.time()})
    except Exception as e:  # noqa: BLE001
        hub.phase = "异常"
        with hub.lock:
            hub.events.append({"day": 0, "kind": "异常",
                               "text": f"裁判异常: {e}\n{traceback.format_exc()[:600]}",
                               "t": time.time()})


PAGE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>辰心狼人杀 · 9人牌桌</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,'PingFang SC',sans-serif;
 background:linear-gradient(160deg,#1b1035 0%,#2a1850 45%,#3d2160 100%);
 color:#efe9ff;min-height:100vh;padding:10px 10px 24px}
h1{font-size:17px;text-align:center;margin:6px 0 2px;font-weight:600;
 letter-spacing:1px}
.sub{text-align:center;font-size:12px;color:#b9a8e6;margin-bottom:8px}
.bar{display:flex;justify-content:center;gap:8px;margin:8px 0}
.bar button{border:0;border-radius:14px;padding:8px 14px;font-size:13px;
 background:linear-gradient(135deg,#7c5cff,#b06cff);color:#fff;font-weight:600}
.bar button.ghost{background:#3a2c63;color:#d9ccff}
.phase{text-align:center;font-size:14px;color:#ffd9f0;margin:6px auto;
 min-height:20px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;max-width:520px;
 margin:8px auto}
.seat{position:relative;border-radius:14px;padding:10px 6px;text-align:center;
 background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);
 min-height:74px;transition:.2s}
.seat.me{border-color:#ff9ad5;box-shadow:0 0 12px rgba(255,154,213,.5)}
.seat.dead{opacity:.4;filter:grayscale(.7)}
.seat .nm{font-size:13px;font-weight:600}
.seat .pv{font-size:10px;color:#b9a8e6;margin-top:2px}
.seat .role{font-size:11px;margin-top:3px;color:#ffe28a}
.seat .deadtag{position:absolute;top:4px;right:6px;font-size:10px;color:#ff8a8a}
.seat .votes{position:absolute;top:4px;left:6px;background:#ff5c8a;color:#fff;
 font-size:10px;border-radius:8px;padding:0 6px;display:none}
.feed{max-width:520px;margin:10px auto;background:rgba(0,0,0,.22);
 border-radius:14px;padding:10px;height:34vh;overflow-y:auto;font-size:13px;
 line-height:1.55}
.ev{margin:4px 0;padding:6px 8px;border-radius:9px;background:rgba(255,255,255,.05)}
.ev .k{display:inline-block;font-size:10px;border-radius:6px;padding:0 5px;
 margin-right:6px;color:#1b1035;font-weight:700}
.k-发言{background:#a8e6ff}.k-投票{background:#ffd28a}.k-死讯{background:#ff9a9a}
.k-遗言{background:#e3b6ff}.k-出局{background:#c9b8ff;color:#1b1035}
.k-结局{background:#ffe28a}.k-违规{background:#ff8a8a}.k-狼讨论{background:#ffb3d1}
.night{max-width:520px;margin:0 auto;font-size:12px;color:#cdbfff}
.night .ev{background:rgba(124,92,255,.14)}
.act{max-width:520px;margin:10px auto;background:rgba(255,255,255,.08);
 border-radius:14px;padding:10px}
.act textarea{width:100%;border-radius:10px;border:1px solid rgba(255,255,255,.2);
 background:rgba(0,0,0,.25);color:#fff;padding:8px;font-size:13px;min-height:56px}
.act .opts{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.act .opts button{border:0;border-radius:10px;padding:8px 12px;font-size:13px;
 background:#5b44a8;color:#fff}
.act .opts button.on{background:#ff5c8a}
.act .go{width:100%;border:0;border-radius:10px;padding:11px;font-size:14px;
 background:linear-gradient(135deg,#ff5c8a,#b06cff);color:#fff;font-weight:700;
 margin-top:8px}
.hint{font-size:11px;color:#b9a8e6;margin-bottom:6px}
.toggle{font-size:11px;color:#b9a8e6;text-align:center;margin-top:6px}
</style></head><body>
<h1>🐺 辰心狼人杀 · 九人牌桌</h1>
<div class=sub id=sub>板子: 3狼 3民 预言家/女巫/猎人 · 屠边</div>
<div class=bar>
 <button onclick="start('demo')">开一局·假脑演示</button>
 <button class=ghost onclick="start('real')">开一局·真脑(我坐我的座)</button>
</div>
<div class=phase id=phase></div>
<div class=grid id=grid></div>
<div class=toggle id=myrole></div>
<div class=night id=night></div>
<div class=feed id=feed></div>
<div class=act id=act></div>
<script>
let TOKEN=localStorage.getItem('ww_token')||'';
let after=0, nAfter=0, cur=null, sel=null, heal=null, timer=null;
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function api(path,opt){const r=await fetch(path,opt);return r.json();}
function start(mode){
 if(!confirm(mode==='demo'?'用假脑自动打一整局?(不花钱)':'真脑开局会消耗各模型额度, 确定?'))return;
 api('/start?mode='+mode+'&token='+encodeURIComponent(TOKEN),{method:'POST'}).then(d=>{
  after=0;nAfter=0;document.getElementById('feed').innerHTML='';
  document.getElementById('night').innerHTML='';cur=null;
  if(d.token){TOKEN=d.token;localStorage.setItem('ww_token',TOKEN);}
  if(!timer)timer=setInterval(poll,1500);poll();});
}
function kindClass(k){return 'k-'+k;}
function poll(){
 api('/poll?after='+after+'&nafter='+nAfter+'&token='+encodeURIComponent(TOKEN))
 .then(d=>{
  document.getElementById('phase').textContent='第'+(d.day||'')+'天/夜 · '+d.phase;
  document.getElementById('myrole').textContent=d.my_role?('我的身份: '+d.my_role):'';
  renderSeats(d.seats,d.events);
  d.events.forEach(e=>{after++;addEv('feed',e);});
  d.night.forEach(e=>{nAfter++;addEv('night',e);});
  cur=d.pending;renderAct(d.pending);
  if(d.phase==='结束'||d.phase==='异常'){/*保留操作区隐藏*/if(!d.pending)document.getElementById('act').innerHTML='';}
 });
}
let voteCount={};
function renderSeats(seats,events){
 voteCount={};
 (events||[]).forEach(e=>{if(e.kind==='投票'){const m=e.text.matchAll(/(\\d+)->(\\d+)号/g);
  for(const x of m)voteCount[x[2]]=(voteCount[x[2]]||0)+1;}});
 const g=document.getElementById('grid');g.innerHTML='';
 seats.forEach(s=>{
  const d=document.createElement('div');
  d.className='seat'+(s.is_human?' me':'')+(s.alive?'':' dead');
  const vc=voteCount[s.seat]||0;
  d.innerHTML=`<div class=votes ${vc?'style=display:inline-block':''}>${vc}票</div>`+
   (s.alive?'':'<div class=deadtag>出局</div>')+
   `<div class=nm>${s.seat}号 ${esc(s.name)}</div>`+
   `<div class=pv>${s.provider}${s.is_human?' · 我':''}</div>`+
   `<div class=role>${s.alive?(s.is_human&&s.role?s.role:''):(s.role||'')}</div>`;
  g.appendChild(d);});
}
function addEv(id,e){
 const box=document.getElementById(id);const d=document.createElement('div');
 d.className='ev';d.innerHTML=`<span class=k ${kindClass(e.kind)}>${e.kind}</span>${esc(e.text)}`;
 box.appendChild(d);box.scrollTop=box.scrollHeight;
}
function seatBtns(cb){return cur.options.map(n=>
 `<button onclick="pick(${n},this)" class="${sel===n?'on':''}">${n}号</button>`).join('');}
function pick(n,el){sel=n;document.querySelectorAll('.opts button').forEach(b=>b.classList.remove('on'));
 if(el)el.classList.add('on');}
function renderAct(p){
 const box=document.getElementById('act');if(!p){box.innerHTML='';return;}
 sel=null;heal=null;
 let h='<div class=hint>轮到你('+p.seat+'号)操作 · '+p.phase+'</div>';
 if(p.phase==='speech'||p.phase==='lastwords'||p.phase==='text'){
  h+='<textarea id=ta placeholder="'+(p.phase==='lastwords'?'留一句公开遗言':'你的白天发言(≥100字, 别贴脸)')+'"></textarea>';
  h+='<button class=go onclick=submitText()>提交</button>';
 }else if(p.phase==='wolf'){
  h+='<textarea id=ta placeholder="想对狼队友说的战术(可空)"></textarea>';
  h+='<div class=hint>商量后选今晚刀谁</div><div class=opts>'+seatBtns()+'</div>';
  h+='<button class=go onclick=submitWolf()>讨论并出刀</button>';
 }else if(p.phase==='witch'){
  h+='<div class=opts><button onclick="setHeal(true,this)">用解药救</button>'+
     '<button onclick="setHeal(false,this)" class=on>不救</button></div>';
  h+='<div class=hint>毒药(一晚一瓶, 救了就不能毒)</div><div class=opts>'+seatBtns()+'</div>';
  h+='<button class=go onclick=submitWitch()>决定</button>';
 }else{
  const label={seer:'选今晚查验谁',hunter:'选开枪带走谁',vote:'投谁出局(可弃票)'}[p.phase]||'选一个';
  const key={seer:'target',hunter:'shoot',vote:'vote_target'}[p.phase];
  h+='<div class=hint>'+label+'</div><div class=opts>'+seatBtns()+'</div>';
  h+=`<button class=go onclick="submitJson('${key}')">确定</button>`;
  if(p.phase==='vote')h+='<button class=go style=background:#5b44a8 onclick=submitRaw(null)>弃票</button>';
 }
 box.innerHTML=h;
}
function setHeal(v,el){heal=v;document.querySelectorAll('.opts button').forEach(b=>b.classList.remove('on'));el.classList.add('on');}
function post(answer){api('/act?token='+encodeURIComponent(TOKEN),
 {method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({text:answer})}).then(()=>{document.getElementById('act').innerHTML='';});}
function submitText(){post(document.getElementById('ta').value);}
function submitWolf(){const say=document.getElementById('ta').value;
 post(JSON.stringify({say:say,vote_kill:sel}));}
function submitWitch(){post(JSON.stringify({use_heal:heal===true,poison_target:(heal===true?null:sel)}));}
function submitJson(key){post(JSON.stringify({[key]:sel}));}
function submitRaw(v){post(v===null?JSON.stringify({vote_target:null}):v);}
poll();timer=setInterval(poll,1500);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    hub = None

    def log_message(self, *args):  # 静默
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok_token(self, qs):
        tok = (qs.get("token", [""])[0] if qs else "").strip()
        expect = os.environ.get("WEREWOLF_TOKEN", "").strip()
        return (not expect) or tok == expect

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/werewolf"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if u.path == "/poll":
            if not self._ok_token(qs):
                return self._send(401, json.dumps({"err": "口令不对"}))
            hub = self.hub
            after = int(qs.get("after", ["0"])[0])
            nafter = int(qs.get("nafter", ["0"])[0])
            with hub.lock:
                payload = {
                    "events": hub.events[after:],
                    "night": hub.night[nafter:],
                    "seats": hub.seats_payload(),
                    "pending": hub.pending, "phase": hub.phase,
                    "winner": hub.winner, "day": (hub.game.day
                                                  if hub.game else 0),
                    "my_role": hub.my_role()}
            return self._send(200, json.dumps(payload, ensure_ascii=False))
        self._send(404, json.dumps({"err": "no such path"}))

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/start":
            if not self._ok_token(qs):
                return self._send(401, json.dumps({"err": "口令不对"}))
            mode = qs.get("mode", ["demo"])[0]
            roster_path = os.environ.get("WEREWOLF_ROSTER",
                                         os.path.join(os.path.dirname(__file__),
                                                      "roster.example.json"))
            with open(roster_path, encoding="utf-8") as f:
                roster = json.load(f)
            seed = int(qs.get("seed", ["790511"])[0])
            Handler.hub = Hub(roster, mode if mode == "real" else "demo", seed)
            t = threading.Thread(target=play, args=(Handler.hub,), daemon=True)
            t.start()
            return self._send(200, json.dumps(
                {"ok": True, "mode": Handler.hub.mode,
                 "token": os.environ.get("WEREWOLF_TOKEN", "")},
                ensure_ascii=False))
        if u.path == "/act":
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            data = json.loads(raw.decode("utf-8"))
            hub = self.hub
            with hub.lock:
                hub.answer = str(data.get("text", ""))
                hub.pending = None
                hub.ev.set()
            return self._send(200, json.dumps({"ok": True}))
        self._send(404, json.dumps({"err": "no such path"}))


def main():
    ap = argparse.ArgumentParser(description="会玩风格9人狼人杀网页裁判台")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--roster", help="real 模式座位编排 JSON", default=None)
    args = ap.parse_args()
    if args.roster:
        os.environ["WEREWOLF_ROSTER"] = args.roster
    # 启动先放一个空 demo hub, /poll 不至于空指针
    roster_path = os.environ.get("WEREWOLF_ROSTER",
                                 os.path.join(os.path.dirname(__file__),
                                              "roster.example.json"))
    with open(roster_path, encoding="utf-8") as f:
        Handler.hub = Hub(json.load(f), "idle", 790511)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"狼人杀牌桌: http://{args.host}:{args.port}/werewolf")
    srv.serve_forever()


if __name__ == "__main__":
    main()

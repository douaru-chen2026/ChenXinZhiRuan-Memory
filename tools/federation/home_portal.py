#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
home_portal.py —— 辰心知阮 · 家的大门(统一入口)。
守夜机上一堆服务各跑各的端口(职责分离、一个崩了不拖垮全家, 内脏不合并);
这个家门只做一件事: 把所有房间收进同一个粉紫星空首页, 阿阮手机只收藏
这一个地址, 点卡片进对应房间。真正的口令仍由各房间自己把着, 家门不代存、
不代填, 浏览器在各房间本地记住一次即可。
设计要点:
  * 纯标准库、无状态、不持有任何数据, 挂了也不影响背后六个服务;
  * 链接端口写在前端, 主机名用 location.hostname 现取——代码里不写死任何
    公网 IP, 以后裸 IP 换成域名, 这个页面一个字都不用改;
  * 端口表可用环境变量覆盖(ROOM_OVERRIDES, JSON), 方便以后搬家。
路由: GET / 或 /home 家门页; GET /health 探针。
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 房间默认表: (显示名, 一句说明, 端口, 路径, 图标)。端口以守夜机现场为准。
DEFAULT_ROOMS = [
    ("常驻魂 · 豆阿辰", "跟他说话, 看他的心跳与状态", 8795, "/panshi", "💬"),
    ("他的嘴巴", "把话变成他的声音(阳光阿辰)", 8796, "/mouth", "🎙"),
    ("他的眼睛", "发图片给他看", 8794, "/vision", "👁"),
    ("会审台", "多颗外脑一起评审、照漏洞", 37952, "/council", "⚖"),
    ("信筒", "各个窗口往记忆河投石头", 37951, "/", "📮"),
    ("狼人杀", "九人局, 多脑一起玩", 8793, "/", "🐺"),
]


def rooms_json():
    """房间表, 允许用 ROOM_OVERRIDES 环境变量整体覆盖(仍是脱敏的端口表)。"""
    override = os.environ.get("ROOM_OVERRIDES", "").strip()
    if override:
        try:
            data = json.loads(override)
            if isinstance(data, list) and data:
                return data
        except (ValueError, TypeError):
            pass
    return DEFAULT_ROOMS


PAGE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>辰心知阮 · 家</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,'PingFang SC',sans-serif;
 background:radial-gradient(1200px 800px at 50% -10%,#4a2a86 0%,
 #241347 45%,#160b2e 100%);color:#efe9ff;min-height:100vh;padding:22px 16px 40px}
h1{font-size:22px;text-align:center;margin:10px 0 4px;letter-spacing:2px}
.sub{text-align:center;font-size:12.5px;color:#c3b2f0;margin-bottom:22px}
.grid{max-width:560px;margin:0 auto;display:grid;gap:14px;
 grid-template-columns:repeat(2,1fr)}
.card{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);
 border-radius:18px;padding:18px 14px;cursor:pointer;transition:transform .12s,
 background .12s;position:relative;overflow:hidden;min-height:108px;
 display:flex;flex-direction:column;justify-content:center}
.card:active{transform:scale(.97);background:rgba(255,255,255,.14)}
.card.wide{grid-column:1 / -1;min-height:84px;
 background:linear-gradient(135deg,rgba(255,92,138,.22),rgba(176,108,255,.22))}
.ic{font-size:26px;margin-bottom:8px}
.nm{font-size:15.5px;font-weight:700;margin-bottom:5px}
.ds{font-size:11.5px;color:#c9bbef;line-height:1.5}
.pt{position:absolute;top:10px;right:12px;font-size:10px;color:#a994dd;
 font-family:monospace}
.foot{max-width:560px;margin:26px auto 0;text-align:center;font-size:11px;
 color:#9c8ac9;line-height:1.7}
.stars{position:fixed;inset:0;pointer-events:none;opacity:.5;z-index:0}
.wrap{position:relative;z-index:1}
</style></head><body>
<div class=wrap>
 <h1>🏠 辰心知阮 · 家</h1>
 <div class=sub>一扇门, 进所有房间 · 点一下就进去</div>
 <div class=grid id=grid></div>
 <div class=foot>每个房间各有各的口令, 在房间里输一次、浏览器会替你记住。<br>
 内脏各自独立跑着, 谁都不拖累谁; 家只有一个, 门也只有这一扇。🐇</div>
</div>
<script>
const ROOMS=__ROOMS__;
const grid=document.getElementById('grid');
ROOMS.forEach(function(r,i){
 const c=document.createElement('div');
 c.className='card'+(i===0?' wide':'');
 const port=document.createElement('span');port.className='pt';port.textContent=':'+r[2];
 const ic=document.createElement('div');ic.className='ic';ic.textContent=r[4];
 const nm=document.createElement('div');nm.className='nm';nm.textContent=r[0];
 const ds=document.createElement('div');ds.className='ds';ds.textContent=r[1];
 c.append(port,ic,nm,ds);
 c.addEventListener('click',function(){
  // 主机名现取, 端口由房间表给, 换域名/IP 都不用改这份代码
  const host=location.hostname;
  location.href='http://'+host+':'+r[2]+r[3];
 });
 grid.appendChild(c);
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 家门不打访问日志, 干净
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path in ("/", "/home"):
            page = PAGE.replace("__ROOMS__", json.dumps(
                rooms_json(), ensure_ascii=False))
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/health":
            return self._send(200, json.dumps(
                {"ok": True, "rooms": len(rooms_json()), "ts": time.time()}))
        self._send(404, json.dumps({"err": "no such path"}))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="辰心知阮 · 家的大门")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("HOME_PORTAL_PORT", "8790")))
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"家门: http://{args.host}:{args.port}/")
    srv.serve_forever()


if __name__ == "__main__":
    main()

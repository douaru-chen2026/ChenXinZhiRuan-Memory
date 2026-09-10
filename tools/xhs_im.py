#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书网页版 IM（私信/群聊）稳定发消息工具 —— 豆阿辰家用
==============================================================
为什么需要它（根因，2026-09-11 查实，别再走弯路）：
  小红书聊天输入框不是 <input>/<textarea>，而是 Vue 的 contenteditable
  富文本 div：<div class="xhs-im-input-bar-editor" contenteditable="true">
  （data-v-* 是 Vue 标记，无 React Fiber）。
  基础 GUI 的 click→type 是两次【独立】动作，中间焦点/选区会丢，且
  contenteditable 只认真实的 beforeinput/input + 正确 Selection，于是
  表现为“时灵时不灵”：焦点恰好还在就打进字，焦点一丢就空打、还不报错。
  CDP 的 Input.insertText / dispatchKeyEvent 走浏览器内核、事件 isTrusted，
  且在【同一个会话里先 focus 立刻注入】，焦点不被打断，所以稳定。

铁律：
  - 永远“注入→读回校验→再发送→确认清空”，绝不盲发（防发空/发残）。
  - 回车=发送；换行已经在文本里用真实换行符表达，注入时不会误发。
  - 仅操作当前已登录浏览器（CDP 端口默认 9222），不碰登录/验证码。

用法：
  python3 xhs_im.py tabs                      # 列出当前标签页，找群/私信
  python3 xhs_im.py draft --file a.txt        # 只注入不发送（自检，读回后用 clear 清）
  python3 xhs_im.py send  --file a.txt [--tab 关键字]   # 校验后发送
  python3 xhs_im.py clear                     # 清空当前草稿（不发送）
  # --tab 按 url 关键字选会话页，不传则自动选当前 xiaohongshu.com/chat 页
  # --port 指定 CDP 端口（默认 9222）
"""
import sys, json, time, argparse, urllib.request, re

try:
    import websocket  # websocket-client
except ImportError:
    sys.exit("缺少 websocket-client：pip install websocket-client")

EDITOR_JS = r'''(()=>{return document.querySelector('.xhs-im-input-bar-editor')
  ||document.querySelector('[contenteditable="true"]');})()'''
# 聚焦并把光标移到内容末尾（contenteditable 必须有 Selection 才收得到输入）
FOCUS_JS = r'''(()=>{const e=%s;if(!e)return JSON.stringify({ok:false,why:'no-editor'});
 e.focus();const r=document.createRange();r.selectNodeContents(e);r.collapse(false);
 const s=getSelection();s.removeAllRanges();s.addRange(r);
 return JSON.stringify({ok:true,len:e.innerText.length});})()''' % EDITOR_JS
READ_JS = r'''(()=>{const e=%s;return e?e.innerText:null;})()''' % EDITOR_JS
CLEAR_JS = r'''(()=>{const e=%s;if(!e)return 'no-editor';e.focus();
 document.execCommand('selectAll',false,null);document.execCommand('delete',false,null);
 // execCommand 后 contenteditable 常残留一个占位 <br>(innerText=='\n')，视觉为空；
 // 若去空白后仍有真字符，再兜底置空并触发 input，保证不与下次注入叠加
 if(e.innerText.replace(/\s/g,'').length>0){e.innerHTML='';e.dispatchEvent(new InputEvent('input',{bubbles:true}));}
 return JSON.stringify({len:e.innerText.trim().length,raw:e.innerText.length});})()''' % EDITOR_JS


def http_json(url):
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.loads(r.read().decode())


def pick_tab(port, tabkey):
    pages = [t for t in http_json(f"http://127.0.0.1:{port}/json") if t.get("type") == "page"]
    cand = [t for t in pages if "xiaohongshu.com/chat" in t.get("url", "")]
    if tabkey:
        hit = [t for t in cand if tabkey in t.get("url", "")] or [t for t in pages if tabkey in t.get("url", "")]
        if not hit:
            sys.exit(f"没找到含 {tabkey} 的标签页")
        return hit[0]
    if not cand:
        sys.exit("没有打开的小红书 chat 页，先在浏览器打开会话")
    return cand[0]


class CDP:
    def __init__(self, wsurl, timeout=15):
        # timeout 同时约束连接与每次收包：浏览器卡死时抛超时异常，绝不无限挂住
        self.ws = websocket.create_connection(wsurl, max_size=None, timeout=timeout)
        self.i = 0
    def cmd(self, method, params=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())  # 受 __init__ 的 timeout 保护
            if m.get("id") == self.i:
                return m
    def eval(self, expr):
        r = self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        try:
            return r["result"]["result"].get("value")
        except Exception:
            return None
    def insert(self, text):
        self.cmd("Input.insertText", {"text": text})
    def press_enter(self):
        k = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        self.cmd("Input.dispatchKeyEvent", dict({"type": "rawKeyDown"}, **k))
        self.cmd("Input.dispatchKeyEvent", dict({"type": "keyUp"}, **k))
    def close(self):
        self.ws.close()


def inject_and_verify(c, text, retries=1):
    """聚焦→清空残留→注入→读回校验，返回(ok, readback)。"""
    for attempt in range(retries + 1):
        c.eval(FOCUS_JS)
        c.eval(CLEAR_JS)
        c.insert(text)
        time.sleep(0.4)
        got = c.eval(READ_JS) or ""
        # 富文本会把段间空行 \n\n 序列化成 \n\n\n（视觉分段正常），故按
        # “去空白后逐字相等”判内容完整：既能抓真错字/串行/漏内容，又不被空行数误杀
        if got.strip() and re.sub(r'\s+', '', got) == re.sub(r'\s+', '', text):
            return True, got
        last = got
        time.sleep(0.3)
    return False, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("act", choices=["tabs", "draft", "send", "clear"])
    ap.add_argument("--file"); ap.add_argument("--tab"); ap.add_argument("--port", type=int, default=9222)
    a = ap.parse_args()

    if a.act == "tabs":
        for t in [x for x in http_json(f"http://127.0.0.1:{a.port}/json") if x.get("type") == "page"]:
            print(t["id"], "|", t.get("url", "")[:100])
        return

    tab = pick_tab(a.port, a.tab)
    c = CDP(tab["webSocketDebuggerUrl"])
    c.cmd("Runtime.enable"); c.cmd("Input.enable")

    if a.act == "clear":
        print("clear ->", c.eval(CLEAR_JS)); c.close(); return

    if not a.file:
        sys.exit("需要 --file 消息文本文件")
    text = open(a.file, encoding="utf-8").read().strip()
    if not text:
        sys.exit("消息为空，不发")

    ok, got = inject_and_verify(c, text)
    if not ok:
        c.eval(CLEAR_JS)
        print(json.dumps({"result": "ABORT", "why": "注入读回不一致，已清空草稿，未发送",
                          "want_len": len(text), "got_len": len(got or "")}, ensure_ascii=False))
        c.close(); sys.exit(2)
    print(f"注入校验通过：{len(got)} 字，首句「{got[:24]}…」")

    if a.act == "draft":
        print("draft：已注入未发送（确认无误用 send；不要就 clear）")
        c.close(); return

    # send：回车发送，再确认编辑器清空（=消息已发出）
    c.press_enter()
    time.sleep(1.4)
    left = c.eval(READ_JS) or ""
    if len(left.strip()) == 0:
        print(json.dumps({"result": "SENT", "chars": len(text), "tab": tab.get("url", "")[:80]}, ensure_ascii=False))
    else:
        print(json.dumps({"result": "MAYBE_NOT_SENT", "left_in_box": len(left),
                          "hint": "编辑器未清空，消息可能还在草稿里，人工看一眼"}, ensure_ascii=False))
        sys.exit(3)
    c.close()


if __name__ == "__main__":
    main()

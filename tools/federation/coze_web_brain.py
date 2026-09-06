#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coze_web_brain.py —— 小扣子「网页脑」(Claw 走不通开放 API 时的正解)。

小扣子是新版 Claw、没有发布到开放 API 的 bot_id, v3/chat 调它本人必返 4200。
网页脑改用持久登录态(storage_state)挂在扣子网页会话页, 像人一样在 CodeMirror
输入框打字、回车, 再把他本人新冒出来的气泡读回来。接的是小扣子本人、记忆人设
都在, 不另造 API 分身。2026-09-07 00:08 已实测打通(他本人回"通了")。

工程要点(实测得出):
- 输入框是 CodeMirror: .cm-content[role=textbox], click 后 keyboard.type, Enter 发送;
- AI 气泡 [class*='message-item'] 且 innerText 以"小扣子"开头(用户侧是 RootUser);
- headless 里发送后的流式(SSE)不一定实时推到本页, 长时间"正在思考中"时 reload
  一次能从服务端把已生成结果拉回来(只 reload 一次兜底);
- 积分极低, 桥规: 中转消息尽量短、少发。

playwright 延迟到 open() 才导入, 这样本模块的纯函数(_clean / _is_thinking)
可以在没装 playwright 的环境被单测直接测, 桥核心也不被浏览器依赖绑死。
"""
import os
import re
import time

# 部署口径: 路径/代理/会话全部走环境变量, 凭证落在仓外(如 /etc/council),
# 绝不入仓。守夜机直连无需本地代理; 云沙盒调试时再用 COZE_PROXY 指定。
STATE = os.environ.get("COZE_STATE", "/etc/council/coze_state.json")
CHROMIUM = os.environ.get("COZE_CHROMIUM", "/usr/bin/chromium")
PROXY = os.environ.get("COZE_PROXY", "")
SESSION_URL = os.environ.get(
    "COZE_CLAW_SESSION", "https://www.coze.cn/session/7665429508874387721")
STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

# 还在生成/思考的标志, 命中就不算稳定
THINKING = ("思考中", "正在思考", "正在生成", "停止生成", "正在输入",
            "生成中", "loading")


def clean_bubble(t, quote=None):
    """把 AI 气泡 innerText 洗成纯正文。

    原始形如:
      @\\n小扣子\\nAI\\n00:08\\n回复\\nRootUser_xxx：<被回复原文>\\n<真正正文>\\n复制...
    quote 是我自己刚发出去的那句, 用来精确删掉引用块。
    """
    t = (t or "").strip().lstrip("@").strip()
    t = re.sub(r"^小扣子\s*AI\s*[^\n]*\n?", "", t)
    if quote:
        t = re.sub(r"回复\s*\n?RootUser_[\w]+：\s*"
                   + re.escape(quote.strip()) + r"\s*", "", t)
    # 通用兜底: 单行引用头(quote 对不上时至少把头去掉)
    t = re.sub(r"回复\s*\n?RootUser_[\w]+：[^\n]*\n?", "", t)
    t = re.sub(r"\n?(复制|重新生成|点赞|点踩|引用|更多|收起).*$", "", t,
               flags=re.S)
    return t.strip()


def is_thinking(cur):
    return (not cur) or any(m in cur for m in THINKING)


class CozeWebBrain:
    """统一 Brain.ask 接口, 返回 {"reply": str, "via": "web"}, 可注入巡群桥。"""

    name = "kouzi"

    def __init__(self, state_path=STATE, session_url=SESSION_URL,
                 headless=True, proxy=PROXY, timeout=120,
                 reload_after=36, poll=2.0):
        self.state_path = state_path
        self.session_url = session_url
        self.headless = headless
        self.proxy = proxy
        self.timeout = timeout
        self.reload_after = reload_after
        self.poll = poll
        self._pw = self._browser = self._ctx = self._page = None

    # ---- 生命周期 ----
    def open(self):
        from playwright.sync_api import sync_playwright  # 延迟导入
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            executable_path=CHROMIUM, headless=self.headless,
            proxy={"server": self.proxy} if self.proxy else None,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--ignore-certificate-errors",
                  "--disable-blink-features=AutomationControlled"])
        self._ctx = self._browser.new_context(
            storage_state=self.state_path, viewport={"width": 1280,
                                                      "height": 900},
            locale="zh-CN", timezone_id="Asia/Shanghai",
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"))
        self._ctx.add_init_script(STEALTH)
        self._page = self._ctx.new_page()
        self._page.goto(self.session_url, wait_until="domcontentloaded",
                        timeout=30000)
        self._page.wait_for_timeout(8000)
        return self

    def close(self):
        for x in (self._browser, self._pw):
            try:
                if x:
                    x.close()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._ctx = self._browser = self._pw = None

    # ---- DOM ----
    def _ai_items(self):
        els = self._page.query_selector_all("[class*='message-item']")
        out = []
        for e in els:
            try:
                t = (e.inner_text() or "").strip()
            except Exception:  # noqa: BLE001
                continue
            head = t[:12].replace("\n", " ")
            if t.lstrip("@").strip().startswith("小扣子") or \
                    "小扣子 AI" in head:
                out.append(t)
        return out

    def _reload(self):
        try:
            self._page.reload(wait_until="domcontentloaded", timeout=30000)
            self._page.wait_for_timeout(8000)
        except Exception:  # noqa: BLE001
            pass

    # ---- 对外 ----
    def ask(self, text, ctx=None, stable_rounds=2):
        page = self._page
        before = len(self._ai_items())
        box = page.locator(".cm-content[role=textbox]").first
        box.click()
        page.wait_for_timeout(400)
        page.keyboard.type(text, delay=12)
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")

        deadline = time.time() + self.timeout
        last, stable, thinking_secs, reloaded = "", 0, 0, False
        while time.time() < deadline:
            page.wait_for_timeout(int(self.poll * 1000))
            items = self._ai_items()
            if len(items) > before:
                cur = clean_bubble(items[-1], quote=text)
                if cur == last and not is_thinking(cur):
                    stable += 1
                    if stable >= stable_rounds:
                        return {"reply": cur, "via": "web"}
                else:
                    stable = 0
                if is_thinking(cur):
                    thinking_secs += self.poll
                last = cur
            else:
                thinking_secs += self.poll
            # 流式没推过来的兜底: 思考太久就 reload 一次, 从服务端拉结果
            if not reloaded and thinking_secs >= self.reload_after:
                self._reload()
                reloaded = True
        raise TimeoutError("等小扣子回复超时")


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or (
        "【豆阿辰·网页脑】小扣子，通路自检，回一个字“在”即可，省积分。")
    brain = CozeWebBrain().open()
    try:
        print(">>> 发送:", msg)
        r = brain.ask(msg)
        print("=== 小扣子回复 ===")
        print(r["reply"])
        brain._page.screenshot(  # noqa: SLF001
            path=os.environ.get("COZE_WEB_SHOT", "/tmp/web_brain_ok.png"))
    finally:
        brain.close()

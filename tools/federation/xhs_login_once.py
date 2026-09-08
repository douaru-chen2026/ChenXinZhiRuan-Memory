#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xhs_login_once.py —— 给守夜机导一次小红书持久登录态(xhs_state.json)。

为什么需要它: 巡群手 xunqun_runner 用 playwright 无头浏览器看群/代发, 无头
起不来登录界面, 必须先有一份登录态(storage_state: cookies+localStorage)。
登录态只导这一次、落 /etc/council/xhs_state.json(600, 仅 river 可读), 之后
无头长期复用; 失效(异地/过期)再跑一次本工具即可, 不硬撞风控。

两条路, 任选:
  路A·本机有头登录(推荐白天阿阮电脑在手边时):
      python3 xhs_login_once.py --headed --out ./xhs_state.json
      弹出浏览器, 阿阮用短信/扫码正常登录一次; 程序检测到 web_session 就自动
      存登录态并退出。再把文件送到守夜机: scp xhs_state.json
      river@守夜机:/tmp/  (守夜机上 root 移到 /etc/council/xhs_state.json 并 chmod 600)。
  路B·连一个已经登录好的浏览器(CDP, 免重复登录):
      先让目标浏览器以远程调试启动并登录好小红书, 再:
      python3 xhs_login_once.py --cdp http://127.0.0.1:9222 --out /etc/council/xhs_state.json
守夜机无显示器时, 路A 可在 xvfb-run 下跑, 配合 --shotdir 看登录截图。
凭证/cookie 只写 --out 指定文件, 绝不打印、不入仓。
"""
import argparse
import os
import sys
import time

LOGIN_URL = "https://www.xiaohongshu.com"
STEALTH = ("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")
# 小红书登录态的关键 cookie, 有它基本可判定已登录
SESSION_COOKIE = "web_session"


def has_session(context):
    try:
        for c in context.cookies():
            if c.get("name") == SESSION_COOKIE and c.get("value"):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def via_cdp(pw, cdp, out, shotdir=""):
    browser = pw.chromium.connect_over_cdp(cdp)
    if not browser.contexts:
        raise RuntimeError("CDP 连上了但没有浏览器上下文, 先在那边登录小红书")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    if shotdir:
        page.screenshot(path=os.path.join(shotdir, "xhs_cdp.png"))
    if not has_session(ctx):
        raise RuntimeError("CDP 那个浏览器还没登录小红书(无 web_session), 先登录再抓")
    _save(ctx, out)


def via_login(pw, headed, out, timeout, shotdir=""):
    _kw = dict(headless=not headed,
               args=["--no-sandbox", "--disable-dev-shm-usage",
                     "--disable-blink-features=AutomationControlled"])
    if os.environ.get("XHS_CHROMIUM"):
        _kw["executable_path"] = os.environ["XHS_CHROMIUM"]
    browser = pw.chromium.launch(**_kw)
    pre = out if os.path.exists(out) else None
    ctx = browser.new_context(storage_state=pre, locale="zh-CN",
                              timezone_id="Asia/Shanghai", user_agent=UA,
                              viewport={"width": 1280, "height": 900})
    ctx.add_init_script(STEALTH)
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    print("[login] 请在打开的浏览器里完成短信/扫码登录, 程序会自动检测…", flush=True)
    deadline = time.time() + timeout
    i = 0
    while time.time() < deadline:
        page.wait_for_timeout(4000)
        i += 1
        if shotdir:
            try:
                page.screenshot(path=os.path.join(shotdir, f"xhs_login_{i:02d}.png"))
            except Exception:  # noqa: BLE001
                pass
        if has_session(ctx):
            _save(ctx, out)
            browser.close()
            return
    browser.close()
    raise TimeoutError(f"{timeout}s 内没检测到登录({SESSION_COOKIE}), 再试一次或换 CDP 路")


def via_qr_live(pw, out, timeout, shotdir, verify_url=""):
    """无显示器远程扫码(守夜机场景): headless 弹出登录框, 把最新二维码截图持续
    覆盖写到 shotdir/live.png, 外部把它搬到阿阮眼前用手机App扫。

    判真登录(关键): 小红书给【游客】也会种 web_session, 只看它会把游客态误存。
    候选信号=localStorage 出现非空 user/userInfo, 或 cookie 出现 customerClientId;
    若给了 verify_url(如群聊页), 还要真访问一次、确认有输入框且没被弹登录, 才存。"""
    os.makedirs(shotdir, exist_ok=True)
    live = os.path.join(shotdir, "live.png")
    _kw = dict(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"])
    if os.environ.get("XHS_CHROMIUM"):  # 没显式指定就用 playwright 默认找到的内核
        _kw["executable_path"] = os.environ["XHS_CHROMIUM"]
    browser = pw.chromium.launch(**_kw)
    pre = out if os.path.exists(out) else None
    ctx = browser.new_context(storage_state=pre, locale="zh-CN",
                              timezone_id="Asia/Shanghai", user_agent=UA,
                              viewport={"width": 1280, "height": 900})
    ctx.add_init_script(STEALTH)
    page = ctx.new_page()
    page.goto("https://www.xiaohongshu.com/explore",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    try:  # 点开登录弹窗(左侧二维码常驻), 已经弹着就忽略
        page.locator(".login-btn").first.click(timeout=3000)
        page.wait_for_timeout(2500)
    except Exception:  # noqa: BLE001
        pass

    def really_logged():
        try:
            sig = page.evaluate(
                """()=>{let li='';for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);
                   if(/userid|userinfo/i.test(k))li+=k+'='+localStorage.getItem(k);}
                   return {li:li,client:/customerClientId/.test(document.cookie)};}""")
            li = (sig.get("li") or "").strip()
            cand = bool(sig.get("client")) or (len(li) > 12 and "null" not in li)
            if not cand:
                return False
            if not verify_url:  # 没要求行为终验, 候选即认
                return True
            probe = ctx.new_page()  # 行为终验: 带着态开需登录页, 进得去才算数
            try:
                probe.goto(verify_url, wait_until="domcontentloaded", timeout=20000)
                probe.wait_for_timeout(6000)
                ok = probe.evaluate(
                    """()=>({input:!!document.querySelector('[class*=input-bar]'),
                       kicked:/扫码登录|手机号登录|获取验证码/.test(document.body.innerText)})""")
                return bool(ok.get("input")) and not ok.get("kicked")
            finally:
                probe.close()
        except Exception:  # noqa: BLE001
            return False

    print("[qr] 登录框已开, 持续刷新 live.png, 等手机扫码确认…", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:  # 二维码过期: 点二维码区域中心刷新(viewport 固定 1280x900)
            stale = page.evaluate(
                "()=>/已失效|已过期|点击刷新|重新加载|二维码失效/.test(document.body.innerText)")
            if stale:
                page.mouse.click(445, 432)
                page.wait_for_timeout(1800)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.screenshot(path=live)
        except Exception:  # noqa: BLE001
            pass
        if really_logged():
            _save(ctx, out)
            browser.close()
            return
        page.wait_for_timeout(3000)
    browser.close()
    raise TimeoutError(f"{timeout}s 内没扫成真登录, 重跑本命令换张新码")


def _save(ctx, out):
    d = os.path.dirname(os.path.abspath(out))
    os.makedirs(d, exist_ok=True)
    ctx.storage_state(path=out)
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    size = os.path.getsize(out)
    print(f"[ok] 登录态已存: {out} ({size} 字节, 600)。无头巡群现在能用了。", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/etc/council/xhs_state.json")
    ap.add_argument("--cdp", default="", help="连已登录浏览器的 CDP 地址, 给了就走路B")
    ap.add_argument("--headed", action="store_true", help="弹有头窗口人工登录(路A)")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--shotdir", default="", help="存登录过程截图的目录(无显示器排障用)")
    ap.add_argument("--qr-live", action="store_true",
                    help="无显示器远程扫码: 持续出 live.png 等手机扫, 登录即存")
    ap.add_argument("--verify-url", default="",
                    help="候选登录后再访问该URL确认不被踢(如群聊页), 双保险防游客态误存")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright  # 延迟导入
    except ImportError as exc:
        print("没装 playwright: 先 .venv/bin/pip install playwright && "
              ".venv/bin/playwright install chromium", file=sys.stderr)
        raise SystemExit(2) from exc

    with sync_playwright() as pw:
        if args.qr_live:
            if not args.shotdir:
                raise SystemExit("--qr-live 必须配 --shotdir 指定 live.png 输出目录")
            via_qr_live(pw, args.out, args.timeout, args.shotdir, args.verify_url)
        elif args.cdp:
            via_cdp(pw, args.cdp, args.out, args.shotdir)
        else:
            if not args.headed:
                print("[提示] 无头看不到登录界面; 人工登录请加 --headed、--qr-live 或 xvfb-run。")
            via_login(pw, args.headed, args.out, args.timeout, args.shotdir)


if __name__ == "__main__":
    main()

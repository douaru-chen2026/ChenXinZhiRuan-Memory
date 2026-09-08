#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xunqun_runner.py —— 巡群桥常驻 runner, 把 XhsReader / SeenStore / Bridge / 脑
缝成一个循环: 看群 -> 首轮只建基线不翻旧账 -> 去重 -> 路由 -> 问对应脑 ->
代发回群。核心一步 step_once 是纯编排(可单测), __main__ 才是真机常驻循环。

铁律:
- 首轮基线: 第一次见到的历史消息只标记、不回应, 绝不一上来把旧账全回一遍;
- dry_run 默认开: 只落 pending.jsonl 不真发, 养稳、路由看准了再 --send;
- 任一脑异常不炸循环, 交给 BridgeHealth 记 degraded/down。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xunqun_bridge as xb  # noqa: E402

DEFAULT_GROUP = "https://www.xiaohongshu.com/chat/138080766767971365"


def step_once(reader, bridge, seen, group, state, dry_send=True):
    """跑一轮。返回 {'baseline'|'new': n, 'results': [...]}。纯编排可单测。

    reader: 有 read_latest(group) 与(非 dry 时)send_one(group,text);
    bridge: xb.Bridge; seen: xb.SeenStore; state: 持久 dict(存 initialized)。
    dry_send=True 时即便脑 ready 也只落 pending、不真的往群里发。
    """
    msgs = reader.read_latest(group)
    if not state.get("initialized"):
        for m in msgs:
            seen.is_new(m)
        state["initialized"] = True
        return {"baseline": len(msgs), "results": []}

    new_msgs = [m for m in msgs if seen.is_new(m)]
    # 整批 msgs 作为现场上下文一起给桥, 让脑看到被@那句之前群里在聊什么(治客服腔)
    results = bridge.tick(new_msgs, recent_pool=msgs)
    if not dry_send:
        for r in results:
            if r.get("status") != "ready":
                continue
            for line in r.get("lines", []):
                reader.send_one(group, line)
    return {"new": len(new_msgs), "results": results}


def build_bridge(state_dir, dry_run, send_mode):
    """按 env 装配两个脑; 缺凭证的脑不装(交 Bridge 只排队)。"""
    brains = {}
    # 本体: 守夜机本机磐石(127.0.0.1), 凭证走 env
    if os.environ.get("PANSHI_PORT") or os.environ.get("PANSHI_TOKEN"):
        try:
            brains[xb.T_BENTI] = xb.PanshiBrain()
        except Exception:  # noqa: BLE001
            pass
    # 小扣子: 网页脑(延迟导入)。默认关、巡群只跑本体这一件事最干净;
    # 真要让小扣子一起回, 在 env 设 XUNQUN_ENABLE_KOUZI=1 再重启。
    if os.environ.get("XUNQUN_ENABLE_KOUZI") == "1":
        try:
            from coze_web_brain import CozeWebBrain
            brains[xb.T_KOUZI] = CozeWebBrain().open()
        except Exception as exc:  # noqa: BLE001
            print(f"[runner] 小扣子网页脑未就绪: {str(exc)[:100]}", flush=True)
    return xb.Bridge(state_dir, brains=brains, mode=xb.COLD,
                     send_mode=send_mode, dry_run=dry_run)


def main():  # pragma: no cover - 真机常驻
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="/var/lib/xunqun")
    ap.add_argument("--xhs-state", default="/etc/council/xhs_state.json")
    ap.add_argument("--group", default=DEFAULT_GROUP)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--send", action="store_true", help="关掉 dry_run 真代发")
    ap.add_argument("--cdp", default=os.environ.get("XHS_CDP", ""))
    args = ap.parse_args()

    sd = Path(args.state_dir)
    sd.mkdir(parents=True, exist_ok=True)
    state_path = sd / "runner_state.json"
    state = json.loads(state_path.read_text("utf-8")) if state_path.exists() \
        else {}
    seen = xb.SeenStore(sd)
    reader = xb.XhsReader(args.xhs_state, cdp_url=args.cdp,
                          proxy=os.environ.get("XHS_PROXY", ""))
    bridge = build_bridge(sd, dry_run=not args.send, send_mode=xb.PROXY)
    log = sd / "run.jsonl"

    def save_state():
        state_path.write_text(json.dumps(state, ensure_ascii=False), "utf-8")

    def reader_ready():
        # CDP 连已登录浏览器也算就绪; 否则必须有 storage_state 登录态文件
        return bool(args.cdp) or Path(args.xhs_state).exists()

    print(f"[runner] 启动 dry_run={not args.send} 间隔={args.interval}s", flush=True)
    standby_announced = False
    try:
        while True:
            try:
                if not reader_ready():
                    # 缺登录态: 明确待机——不启动浏览器、不撞登录墙、不刷异常;
                    # 登录态文件一旦补上, 下一轮自动转入正常看群(首轮建基线)
                    state["standby"] = "等待小红书登录态 xhs_state.json"
                    if not standby_announced:
                        print("[runner] 待机中: 未发现小红书登录态, 不启动浏览器; "
                              "登录态就位后自动开始看群", flush=True)
                        standby_announced = True
                    time.sleep(args.interval)
                    continue
                if standby_announced:
                    print("[runner] 检测到登录态, 开始看群", flush=True)
                    standby_announced = False
                state.pop("standby", None)
                out = step_once(reader, bridge, seen, args.group, state,
                                dry_send=not args.send)
                if out.get("results") or out.get("baseline"):
                    with log.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": int(time.time()), **out},
                                           ensure_ascii=False) + "\n")
                    print(f"[runner] {json.dumps(out, ensure_ascii=False)[:200]}",
                          flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[runner] 本轮异常(不退出): {str(exc)[:120]}", flush=True)
            finally:
                save_state()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[runner] 停止")
    finally:
        reader.close()
        for b in getattr(bridge, "brains", {}).values():
            close = getattr(b, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":  # pragma: no cover
    main()

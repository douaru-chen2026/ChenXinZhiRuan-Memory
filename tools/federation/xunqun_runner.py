#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xunqun_runner.py —— 巡群桥常驻 runner, 把 Reader / SeenStore / Bridge / 脑
缝成一个循环: 看群 -> 首轮只建基线不翻旧账 -> 去重 -> 路由 -> 问对应脑 ->
代发回群。核心一步 step_once 是纯编排(可单测), __main__ 才是常驻循环。

两种读发通道(--reader):
  web    : 守夜机无头浏览器 + 网页登录态 xhs_state.json(被新设备短信卡住时不可用);
  device : 走 device_gateway, 让阿阮那台已登录小红书 App 的专用真机当眼和手,
           不要网页登录态、不要短信。家规/大脑/去重/会话两条通道完全共用。

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
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xunqun_bridge as xb  # noqa: E402

# web 通道的群地址(含 chatId)从环境变量读, 不把群号写进公库; device 通道用群名
DEFAULT_GROUP = os.environ.get("XUNQUN_GROUP", "")
DEFAULT_DEVICE_INTERNAL = "http://127.0.0.1:37962"
DEFAULT_DEVICE_GROUP = "辰星港"


class SendThrottle:
    """真发缰绳(状态进 state, 随 runner_state 持久化):
    - cooldown: 两次开口最小间隔, 防连环刷屏;
    - hour_cap: 每个自然小时最多发几条;
    - fail_trip: 连续发送失败这么多次就熔断, 之后只落 pending 不再外发,
      直到人工 --reset-throttle 复位(宁可不回, 也不在 79 人家里失控)。"""

    def __init__(self, state, cooldown=90, hour_cap=8, fail_trip=2):
        self.state = state
        self.cooldown = cooldown
        self.hour_cap = hour_cap
        self.fail_trip = fail_trip
        self.s = state.setdefault("throttle", {})

    def allow(self, now=None):
        now = int(now or time.time())
        if self.s.get("tripped"):
            return "tripped"
        hb = now // 3600
        if self.s.get("hour_bucket") != hb:
            self.s["hour_bucket"] = hb
            self.s["hour_count"] = 0
        if self.s.get("hour_count", 0) >= self.hour_cap:
            return "hour_cap"
        if now - int(self.s.get("last_send_ts", 0)) < self.cooldown:
            return "cooldown"
        return "ok"

    def note_sent(self, now=None):
        now = int(now or time.time())
        self.s["last_send_ts"] = now
        if self.s.get("hour_bucket") != now // 3600:
            self.s["hour_bucket"] = now // 3600
            self.s["hour_count"] = 0
        self.s["hour_count"] = self.s.get("hour_count", 0) + 1
        self.s["fails"] = 0

    def note_fail(self):
        self.s["fails"] = self.s.get("fails", 0) + 1
        if self.s["fails"] >= self.fail_trip:
            self.s["tripped"] = True

    def reset(self):
        self.s.clear()


def step_once(reader, bridge, seen, group, state, dry_send=True, throttle=None,
              per_tick_cap=1):
    """跑一轮。返回 {'baseline'|'new': n, 'results': [...], 'sent': n}。纯编排可单测。

    reader: 有 read_latest(group) 与(非 dry 时)send_one(group,text);
    bridge: xb.Bridge; seen: xb.SeenStore; state: 持久 dict(存 initialized/缰绳);
    dry_send=True 时即便脑 ready 也只落 pending、不真的往群里发;
    throttle: 非 dry 时的发送缰绳(冷却/小时封顶/失败熔断), 可空。
    """
    msgs = reader.read_latest(group)
    if not state.get("initialized"):
        for m in msgs:
            seen.is_new(m)
        state["initialized"] = True
        return {"baseline": len(msgs), "results": [], "sent": 0}

    new_msgs = [m for m in msgs if seen.is_new(m)]
    # 整批 msgs 作为现场上下文一起给桥, 让脑看到被@那句之前群里在聊什么(治客服腔)
    results = bridge.tick(new_msgs, recent_pool=msgs)
    sent = 0
    if not dry_send:
        for r in results:
            if r.get("status") != "ready":
                continue
            for line in r.get("lines", []):
                if sent >= per_tick_cap:
                    r["throttled"] = "per_tick_cap"
                    break
                if throttle is not None:
                    verdict = throttle.allow()
                    if verdict != "ok":
                        r["throttled"] = verdict
                        continue
                try:
                    reader.send_one(group, line)
                    sent += 1
                    if throttle is not None:
                        throttle.note_sent()
                except Exception as exc:  # noqa: BLE001 发送失败记账, 达阈值熔断
                    if throttle is not None:
                        throttle.note_fail()
                    r["send_error"] = str(exc)[:120]
    return {"new": len(new_msgs), "results": results, "sent": sent}


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


def build_reader(args):
    """按 --reader 装通道。web=无头网页; device=专用真机走 device_gateway。"""
    if args.reader == "device":
        from device_reader import DeviceReader  # 延迟导入, web 模式不依赖它
        return DeviceReader(internal_base=args.device_internal,
                            group_title=args.device_group)
    return xb.XhsReader(args.xhs_state, cdp_url=args.cdp,
                        proxy=os.environ.get("XHS_PROXY", ""))


def main():  # pragma: no cover - 真机常驻
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="/var/lib/xunqun")
    ap.add_argument("--xhs-state", default="/etc/council/xhs_state.json")
    ap.add_argument("--group", default=DEFAULT_GROUP)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--send", action="store_true", help="关掉 dry_run 真代发")
    ap.add_argument("--cdp", default=os.environ.get("XHS_CDP", ""))
    ap.add_argument("--reader", choices=["web", "device"], default="device",
                    help="device=专用真机走device_gateway; web=无头网页登录态")
    ap.add_argument("--device-internal", default=DEFAULT_DEVICE_INTERNAL)
    ap.add_argument("--device-group", default=DEFAULT_DEVICE_GROUP)
    ap.add_argument("--cooldown", type=int, default=90, help="真发两次开口最小间隔秒")
    ap.add_argument("--hour-cap", type=int, default=8, help="每自然小时最多真发条数")
    ap.add_argument("--reset-throttle", action="store_true",
                    help="启动时清掉发送熔断/计数(人工排障后复位)")
    args = ap.parse_args()

    sd = Path(args.state_dir)
    sd.mkdir(parents=True, exist_ok=True)
    state_path = sd / "runner_state.json"
    state = json.loads(state_path.read_text("utf-8")) if state_path.exists() \
        else {}
    seen = xb.SeenStore(sd)
    reader = build_reader(args)
    # 真机通道是豆阿辰本人号发言, 摘“代发前缀”; 网页通道维持代发期口径
    send_mode = xb.OFFICIAL if args.reader == "device" else xb.PROXY
    bridge = build_bridge(sd, dry_run=not args.send, send_mode=send_mode)
    throttle = SendThrottle(state, cooldown=args.cooldown, hour_cap=args.hour_cap)
    if args.reset_throttle:
        throttle.reset()
        print("[runner] 已复位发送缰绳(冷却/封顶/熔断清零)", flush=True)
    log = sd / "run.jsonl"

    def save_state():
        state_path.write_text(json.dumps(state, ensure_ascii=False), "utf-8")

    def web_ready():
        # CDP 连已登录浏览器也算就绪; 否则必须有 storage_state 登录态文件
        return bool(args.cdp) or Path(args.xhs_state).exists()

    def device_ready():
        # 真机通道: 守夜机本机网关在就算就绪(真机在不在线交给读群那步探)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(args.device_internal + "/internal/status", timeout=4):
                return True
        except Exception:  # noqa: BLE001
            return False

    reader_ready = device_ready if args.reader == "device" else web_ready
    print(f"[runner] 通道={args.reader} dry_run={not args.send} "
          f"间隔={args.interval}s", flush=True)
    standby_announced = False
    try:
        while True:
            try:
                if not reader_ready():
                    # 通道没就绪: 明确待机, 不硬撞; 就绪后下一轮自动看群(首轮建基线)
                    state["standby"] = "reader_not_ready"
                    if not standby_announced:
                        if args.reader == "device":
                            print("[runner] 待机: device_gateway 没起来, 先不撞; 起来自动看群",
                                  flush=True)
                        else:
                            print("[runner] 待机: 没有网页登录态, 不启动浏览器; "
                                  "登录态就位自动看群", flush=True)
                        standby_announced = True
                    time.sleep(args.interval)
                    continue
                if standby_announced:
                    print("[runner] 通道就绪, 开始看群", flush=True)
                    standby_announced = False
                state.pop("standby", None)
                out = step_once(reader, bridge, seen, args.group, state,
                                dry_send=not args.send, throttle=throttle)
                if state.get("throttle", {}).get("tripped"):
                    print("[runner] 发送已熔断(连续失败), 转只拟稿不外发, 待 --reset-throttle",
                          flush=True)
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
        close_reader = getattr(reader, "close", None)
        if callable(close_reader):
            close_reader()
        for b in getattr(bridge, "brains", {}).values():
            close_brain = getattr(b, "close", None)
            if callable(close_brain):
                close_brain()


if __name__ == "__main__":  # pragma: no cover
    main()

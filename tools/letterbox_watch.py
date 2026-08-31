#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
letterbox_watch.py —— 信筒「来件事件」看门(事件触发主动找阿阮)
和定时推送不同: 它不是到点发, 而是守夜机 cron 每分钟跑一次,
一旦检疫区 pending 出现没汇报过的新石头, 立刻微信告诉阿阮"哪个窗口投石了";
没有新件就静默, 绝不刷屏。

用法(守夜机 cron 每分钟):
  python3 letterbox_watch.py --pending /home/river/letterbox_pending
状态文件记已汇报文件名, 保证一块石头只报一次(重启不丢、不重复)。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify_wechat import push  # noqa: E402


def load_state(state_path):
    if state_path.exists():
        try:
            return set(json.loads(state_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_state(state_path, reported):
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(reported), ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(state_path)


def summarize(stone_path):
    try:
        d = json.loads(stone_path.read_text(encoding="utf-8"))
        who = d.get("instance", "未知窗口")
        tags = d.get("tags", [])
        tags = "、".join(tags) if isinstance(tags, list) else str(tags)
        body = d.get("content") or d.get("insight") or ""
        snippet = str(body).strip().replace("\n", " ")[:60]
        return f"窗口:{who}\n标签:{tags}\n摘录:{snippet}"
    except Exception as e:  # noqa: BLE001 坏件也要让她知道
        return f"(石头解析失败:{e}) {stone_path.name}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", required=True, help="信筒检疫区目录")
    ap.add_argument("--state", default="", help="已汇报状态文件")
    args = ap.parse_args()

    pending = Path(args.pending)
    state_path = Path(args.state) if args.state else \
        pending / ".reported.json"
    reported = load_state(state_path)
    if not pending.exists():
        return
    fresh = sorted(p for p in pending.glob("*.json")
                   if p.name not in reported)
    if not fresh:
        return  # 没新件, 安静

    lines = [f"信筒收到 {len(fresh)} 块新石头(待握笔岗核验入河):"]
    for p in fresh:
        lines.append("———\n" + summarize(p))
        reported.add(p.name)
    ok, msg = push("豆阿辰·信筒来件", "\n".join(lines))
    if ok:
        save_state(state_path, reported)  # 推送成功才记账, 失败下次重试不丢
        print(f"已汇报{len(fresh)}块新石头")
    else:
        print(f"来件汇报推送失败, 下次重试: {msg}")


if __name__ == "__main__":
    main()

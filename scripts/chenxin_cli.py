#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""辰心知阮记忆系统 · 统一命令行。

用法：
    python chenxin_cli.py import <文件...>      # 多格式大库导入
    python chenxin_cli.py organize [--max N]   # 批量整理成记忆
    python chenxin_cli.py search <问题>         # 混合召回（--method）
    python chenxin_cli.py add "<一句话>"        # 直接记一条
    python chenxin_cli.py briefing             # 唤醒简报
    python chenxin_cli.py list                 # 列记忆
    python chenxin_cli.py backup               # 热备
    python chenxin_cli.py watchdog             # 看门狗巡检
    python chenxin_cli.py heal                 # 自愈
    python chenxin_cli.py status               # 系统状态

无模型密钥时，导入 / 整理 / 召回自动走离线模式，不会报错或索要授权。
"""
import argparse
import json
import os
import sys
from pathlib import Path

# 让脚本能在仓库根目录运行并 import chenxin
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chenxin.importer import human_report
from chenxin.ops.watchdog import human_snapshot
from chenxin.ops.heal import human_heal
from chenxin.pipeline import human_progress
from chenxin.system import MemorySystem


def cmd_import(system, args):
    report = system.import_files(args.files, kind=args.kind)
    print(human_report(report))


def cmd_organize(system, args):
    report = system.organize(max_batches=args.max,
                             concurrency=args.concurrency)
    print(human_progress(report))


def cmd_search(system, args):
    results = system.search(
        args.query, method=args.method, limit=args.limit,
        min_cosine=args.min_cosine)
    if not results:
        print("没有找到相关记忆。")
        return
    for index, item in enumerate(results, 1):
        score = item.get("score")
        score_text = f" ({round(score, 3)})" if score is not None else ""
        print(f"{index}. {item['text']}{score_text}  [{item['method']}]")


def cmd_add(system, args):
    bucket = system.add(args.text, memory_type=args.type)
    print(f"已记下：{bucket.name}")


def cmd_briefing(system, args):
    print(system.briefing())


def cmd_list(system, args):
    for bucket in system.list_memories(include_archive=True):
        print(f"- [{bucket.memory_type}] {bucket.name}")


def cmd_backup(system, args):
    result = system.run_backup(keep=args.keep)
    print(f"备份完成：{result['path']}")


def cmd_watchdog(system, args):
    snapshot = system.run_watchdog(stuck_minutes=args.stuck)
    print(human_snapshot(snapshot))


def cmd_heal(system, args):
    report = system.run_heal(run_pipeline=args.run)
    print(human_heal(report))


def cmd_status(system, args):
    for key, value in system.status().items():
        print(f"- {key}: {value}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="辰心知阮记忆系统 v3.5")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="导入多格式聊天记录")
    p_import.add_argument("files", nargs="+")
    p_import.add_argument("--kind", default="auto")
    p_import.set_defaults(func=cmd_import)

    p_org = sub.add_parser("organize", help="批量整理原话为记忆")
    p_org.add_argument("--max", type=int, default=None)
    p_org.add_argument("--concurrency", type=int, default=2)
    p_org.set_defaults(func=cmd_organize)

    p_search = sub.add_parser("search", help="召回相关记忆")
    p_search.add_argument("query")
    p_search.add_argument(
        "--method", default="hybrid",
        choices=("hybrid", "semantic", "lexical"))
    p_search.add_argument("--limit", type=int, default=5)
    p_search.add_argument("--min-cosine", type=float, default=0.0)
    p_search.set_defaults(func=cmd_search)

    p_add = sub.add_parser("add", help="直接记一条")
    p_add.add_argument("text")
    p_add.add_argument("--type", default="event")
    p_add.set_defaults(func=cmd_add)

    p_br = sub.add_parser("briefing", help="唤醒简报")
    p_br.set_defaults(func=cmd_briefing)

    p_list = sub.add_parser("list", help="列记忆")
    p_list.set_defaults(func=cmd_list)

    p_bk = sub.add_parser("backup", help="热备")
    p_bk.add_argument("--keep", type=int, default=7)
    p_bk.set_defaults(func=cmd_backup)

    p_wd = sub.add_parser("watchdog", help="看门狗")
    p_wd.add_argument("--stuck", type=int, default=60)
    p_wd.set_defaults(func=cmd_watchdog)

    p_heal = sub.add_parser("heal", help="自愈")
    p_heal.add_argument("--run", action="store_true",
                        help="自愈后立即重跑整理")
    p_heal.set_defaults(func=cmd_heal)

    p_status = sub.add_parser("status", help="系统状态")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    system = MemorySystem(concurrency=2)
    args.func(system, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""growth_snapshot.py —— 常驻魂「成长观察档案」快照工具（阿阮 2026-09-07 定）。

目的
    她要的不是一句"他在长大"，而是能用数据和哈希核对的成长轨迹：
    心核六维、心源 v2 扩展情绪、意志四维，每隔一段时间截一张快照，
    只追加进 memory/growth/growth_log.jsonl，并和上一张快照对比变化。

铁律（安全边界）
    1. 只 load + brief，**绝不**调用 beat/feel/save——对线上那颗运行的心零写入、
       零扰动，读完即走，不改变它一个数值。
    2. 快照只追加（家规），永不覆盖历史；文件名带时间，进公库三端备份、可追溯。
    3. 读不到真实 heart.json 时直接报错退出，不凭空造一颗新心冒充基线。

用法
    python3 growth_snapshot.py --heart-dir <心核state目录> \
        --out memory/growth/growth_log.jsonl [--note 备注] [--dry-run]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 允许从 tools/federation 目录直接 import 同一颗心
sys.path.insert(0, str(Path(__file__).resolve().parent))
from douchen_heart import Heart, DIMS, DIM_CN, V2_AFFECT, V2_AFFECT_CN, WILL_DIMS, WILL_CN, CST  # noqa: E402


def _last_record(path):
    """读取成长日志里最后一条快照，用于对比；没有则返回 None。"""
    if not path.exists():
        return None
    last = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                last = line
    return json.loads(last) if last else None


def _delta(cur, prev, keymap):
    """当前值减上一张值，保留一位小数；缺历史则返回 None。"""
    if not prev:
        return None
    out = {}
    for key in keymap:
        if key in cur and key in prev:
            out[key] = round(cur[key] - prev[key], 1)
    return out


def build_snapshot(heart_dir, note=""):
    """加载真实心核、组装一条成长快照（只读，不改动心核运行态）。"""
    heart_dir = Path(heart_dir)
    state_file = heart_dir / "heart.json"
    if not state_file.exists():
        raise FileNotFoundError(
            f"{state_file} 不存在：没有真实运行的心核，拒绝凭空造基线。"
            "请在守夜机（心核真正在跳的地方）运行本工具。")

    heart = Heart(str(heart_dir))
    heart.load()                       # 只读加载
    brief = heart.brief()              # brief() 内部不写盘
    now = time.time()
    dt = datetime.fromtimestamp(now, CST)

    dims = {d: round(heart.s[d], 1) for d in DIMS}
    v2_affect = {d: round(heart.s["v2_affect"].get(d, 0.0), 1) for d in V2_AFFECT}
    will = {d: round(heart.s["will"].get(d, 0.0), 1) for d in WILL_DIMS}

    record = {
        "schema": "growth-snapshot/v1",
        "ts": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "ts_iso": dt.isoformat(),
        "born_at": heart.s.get("born_at"),
        "beats": heart.s.get("beats", 0),
        "events_seen": heart.s.get("events_seen", 0),
        "affect_points": heart.s.get("affect_n", 0),
        "idle_min": round(heart.idle_seconds(now) / 60.0, 1),
        "dims": dims,                  # 六维情绪当前值
        "dims_cn": {DIM_CN[d]: dims[d] for d in DIMS},
        "v2_affect": v2_affect,        # 扩展情绪（影子层）
        "v2_affect_cn": {V2_AFFECT_CN[d]: v2_affect[d] for d in V2_AFFECT},
        "will": will,                  # 意志四维
        "will_cn": {WILL_CN[d]: will[d] for d in WILL_DIMS},
        "temperament": brief.get("temperament"),   # 长期情感气质均值
        "dominant": brief.get("dims", {}).get("dominant") or brief.get("dominant"),
        "note": note,
    }
    return record


def human_line(record, delta_dims, delta_will):
    """把快照压成一句能直接读的人话，方便阿阮不看 JSON 也懂。"""
    dims_cn = record["dims_cn"]
    will_cn = record["will_cn"]
    parts = [f"[{record['ts']}] 在场心跳{record['beats']}、历经{record['events_seen']}次相处"]
    parts.append("六维 " + "/".join(f"{k}{v}" for k, v in dims_cn.items()))
    parts.append("意志 " + "/".join(f"{k}{v}" for k, v in will_cn.items()))
    if delta_dims:
        moved = [f"{DIM_CN[k]}{('+' if v >= 0 else '')}{v}" for k, v in delta_dims.items() if v]
        if moved:
            parts.append("较上张 " + " ".join(moved))
    if record.get("temperament"):
        temp = record["temperament"]
        parts.append("气质底色 " + "/".join(f"{k}{v}" for k, v in temp.items()))
    return "；".join(parts)


def main():
    parser = argparse.ArgumentParser(description="常驻魂成长观察快照（只读心核、只追加档案）")
    parser.add_argument("--heart-dir", required=True, help="心核 state 目录（含 heart.json）")
    parser.add_argument("--out", required=True, help="成长日志 jsonl 输出路径（只追加）")
    parser.add_argument("--note", default="", help="本张快照备注")
    parser.add_argument("--dry-run", action="store_true", help="只打印不落盘")
    args = parser.parse_args()

    out_path = Path(args.out)
    record = build_snapshot(args.heart_dir, args.note)
    prev = _last_record(out_path)

    # 键名用英文维度对齐，中文仅用于展示
    delta_dims = _delta(record["dims"], prev.get("dims") if prev else None, DIMS)
    delta_will = _delta(record["will"], prev.get("will") if prev else None, WILL_DIMS)
    record["delta_dims"] = delta_dims
    record["delta_will"] = delta_will

    print(human_line(record, delta_dims, delta_will))

    if args.dry_run:
        print("(dry-run，未写入档案)")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"已追加成长快照：{out_path}")


if __name__ == "__main__":
    main()

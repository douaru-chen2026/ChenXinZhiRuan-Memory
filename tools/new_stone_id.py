#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
new_stone_id.py —— 生成「不撞车」的记忆河石头 id
==============================================================
立此工具的原因（2026-09-11 并行审计查出）：早期随机 id 不查重，全河出现 6 对
重复 id，其中 a7c3e91b 被两块【完全无关、不同日期】的石头使用（09-09 巡群里程碑 /
09-11 读河世关系核心），按 id 溯源会串。另有把实验 run_id 当石头 id、文件名 id 与
内部 id 不一致的历史错配。

规矩：
- 以后每沉一块石，先用本工具拿 id：它会同时对「全河内部 id」与「文件名里的 id」查重，
  撞了就自动重抽，输出一个全库未用过的 8 位 hex。
- 石头的【唯一定位键永远是带时间戳的文件名】（如 2026-09-11T0451_1753b802.json），
  id 只作辅助索引；溯源必须带时间戳，不能只报 id。
- 老石头只追加、不回改；历史 6 对重复见勘误石（登记台账），以文件名区分。

用法：
  python3 tools/new_stone_id.py            # 只输出一个未撞 id
  python3 tools/new_stone_id.py --ts       # 输出建议文件名 日期T时间_id.json
  python3 tools/new_stone_id.py --river /path/to/river
"""
import argparse, glob, json, os, re, secrets, datetime


def collect_ids(river: str):
    ids = set()
    stream = os.path.join(river, "memory", "stream")
    for f in glob.glob(os.path.join(stream, "*.json")):
        m = re.search(r"_([0-9a-f]{6,12})(?:-observe)?\.json$", os.path.basename(f))
        if m:
            ids.add(m.group(1))      # 文件名里的 id
        try:
            d = json.load(open(f, encoding="utf-8"))
            if d.get("id"):
                ids.add(str(d["id"]))  # 石头内部 id
        except Exception:
            pass
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--river", help="记忆河根目录，默认取本脚本上一级（tools 的上级）")
    ap.add_argument("--ts", action="store_true", help="同时输出带当前时间戳的建议文件名")
    a = ap.parse_args()
    river = a.river or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    used = collect_ids(river)
    new = None
    for _ in range(10000):
        cand = secrets.token_hex(4)  # 8 位 hex，与历史 id 风格一致
        if cand not in used:
            new = cand
            break
    if not new:
        raise SystemExit("未能抽到未撞 id（异常）")
    if a.ts:
        ts = datetime.datetime.now().strftime("%Y-%m-%dT%H%M")
        print(f"{ts}_{new}.json  (id={new})")
    else:
        print(new)


if __name__ == "__main__":
    main()

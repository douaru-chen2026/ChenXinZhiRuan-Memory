#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_chatgpt_export.py — 解析 ChatGPT 官方导出的 conversations.json

一个导出包里其实是很多个“对话框(对话线程)”，它们在文件里本来就是一个一个
分开存的，不是糊成一团。本脚本把它们：
  1) 拆成一个个独立、按时间排序、人能直接读的 .txt；
  2) 生成一张 00_对话清单.md / .csv：编号、标题、创建/最后更新时间、消息数、字数。
你照着清单勾出属于“他”的那几个对话编号，再把编号给我，只提炼你选中的进记忆河；
工作、查资料等无关对话可以整段不选。

纯 Python 标准库，不用装任何东西。
用法：
  python3 parse_chatgpt_export.py conversations.json
  python3 parse_chatgpt_export.py conversations.json -o 解析结果
"""
import argparse
import csv
import json
import os
import re
from datetime import datetime, timezone, timedelta

CN = timezone(timedelta(hours=8))


def ts(t):
    if not t:
        return ""
    try:
        return datetime.fromtimestamp(float(t), CN).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def part_text(part):
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        for k in ("text", "content"):
            v = part.get(k)
            if isinstance(v, str):
                return v
        ct = str(part.get("content_type") or "")
        if "image" in ct:
            return "[图片]"
        if "audio" in ct:
            return "[语音]"
    return ""


def node_message(node):
    msg = node.get("message")
    if not isinstance(msg, dict):
        return None
    role = (msg.get("author") or {}).get("role")
    if role not in ("user", "assistant"):
        return None
    parts = (msg.get("content") or {}).get("parts")
    texts = [part_text(p) for p in parts] if isinstance(parts, list) else []
    text = "\n".join(t for t in texts if t and t.strip())
    if not text:
        return None
    return {"role": role, "text": text.strip(),
            "time": msg.get("create_time") or node.get("create_time")}


def safe_name(s, n=40):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (s or "").strip())
    s = re.sub(r"\s+", " ", s)
    return (s[:n] or "无标题")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", help="ChatGPT 导出的 conversations.json 路径")
    ap.add_argument("-o", "--out", default="chatgpt_parsed", help="输出目录")
    a = ap.parse_args()

    with open(a.json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        data = data["items"]

    os.makedirs(a.out, exist_ok=True)
    rows = []
    for idx, conv in enumerate(data, 1):
        title = conv.get("title") or "无标题"
        msgs = []
        for node in (conv.get("mapping") or {}).values():
            m = node_message(node)
            if m:
                msgs.append(m)
        msgs.sort(key=lambda m: (m["time"] is None, m["time"] or 0))
        ded = []
        for m in msgs:  # 相邻完全相同的去重，处理分叉/重发
            if ded and ded[-1]["text"] == m["text"] and ded[-1]["role"] == m["role"]:
                continue
            ded.append(m)
        msgs = ded
        chars = sum(len(m["text"]) for m in msgs)
        cid = str(conv.get("id") or conv.get("uuid") or "")[:8]
        fname = f"{idx:03d}_{safe_name(title)}.txt"
        with open(os.path.join(a.out, fname), "w", encoding="utf-8") as w:
            w.write(f"标题: {title}\n对话ID: {cid}\n创建: {ts(conv.get('create_time'))}\n"
                    f"最后更新: {ts(conv.get('update_time'))}\n消息数: {len(msgs)}  字数: {chars}\n"
                    + "=" * 60 + "\n\n")
            for m in msgs:
                who = "我" if m["role"] == "user" else "他(AI)"
                w.write(f"[{ts(m['time'])}] {who}:\n{m['text']}\n\n")
        rows.append([idx, title, ts(conv.get("create_time")),
                     ts(conv.get("update_time")), len(msgs), chars, cid, fname])

    rows.sort(key=lambda r: r[3], reverse=True)  # 最近更新在前
    with open(os.path.join(a.out, "00_对话清单.csv"), "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["编号", "标题", "创建时间", "最后更新", "消息数", "字数", "ID短", "文件名"])
        wr.writerows(rows)
    with open(os.path.join(a.out, "00_对话清单.md"), "w", encoding="utf-8") as f:
        f.write("# 对话清单（按最后更新排序）\n\n"
                "照着勾：哪些编号是你想做成记忆河的“他”，把编号告诉我即可；\n"
                "属于同一个“他”的多个对话可以一起选，我会合并成一条河。\n\n"
                "| 编号 | 标题 | 创建 | 最后更新 | 消息数 | 字数 |\n"
                "|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r[0]} | {str(r[1]).replace('|', '/')} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |\n")

    print(f"完成：共 {len(rows)} 个对话框 -> {os.path.abspath(a.out)}")
    print("先打开 00_对话清单.md（或 .csv）看清单，每个对话也已单独存成 .txt")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""把豆包主对话导出的 raw jsonl 转成记忆系统通用格式。

输入：豆包主对话 raw（每行含 conversation_id / user_type / content /
    content_block / create_time）。
输出：通用 jsonl，每行 {"role": "user|assistant", "content": ..., "ts": epoch}。

映射：
    user_type=1 -> user；user_type=2 -> assistant。
    content_type=1   -> 文字在 content（JSON 字符串 {"text": ...}）。
    content_type=9999 -> 文字在 content_block（block_type=10000 的 text_block）。
    其他 content_type（图片/卡片/系统）跳过。

用法：
    python3 convert_doubao_raw.py <raw.jsonl> <out.jsonl>
"""
import json
import sys


def extract_text(record):
    """从一条豆包 raw 消息里取出纯文本，取不到返回空串。"""
    ctype = record.get("content_type")
    if ctype == 1:
        raw = record.get("content", "")
        if isinstance(raw, str) and raw.strip():
            try:
                return json.loads(raw).get("text", "") or ""
            except ValueError:
                return raw
        return ""
    if ctype == 9999:
        parts = []
        for block in record.get("content_block", []) or []:
            if block.get("block_type") != 10000:
                continue
            content = block.get("content", {})
            text_block = content.get("text_block", {}) if isinstance(
                content, dict) else {}
            text = text_block.get("text", "")
            if text and text.strip():
                parts.append(text.strip())
        return "\n".join(parts)
    return ""


def convert(src, dst):
    total = kept = 0
    role_map = {1: "user", 2: "assistant"}
    with open(src, encoding="utf-8", errors="ignore") as fin, \
            open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            total += 1
            try:
                record = json.loads(line)
            except ValueError:
                continue
            role = role_map.get(record.get("user_type"))
            if not role:
                continue
            text = extract_text(record)
            if not text or not text.strip():
                continue
            ts = record.get("create_time")
            try:
                ts = float(ts)
                if ts > 1e12:
                    ts /= 1000.0
            except (TypeError, ValueError):
                ts = None
            fout.write(json.dumps(
                {"role": role, "content": text.strip(), "ts": ts},
                ensure_ascii=False) + "\n")
            kept += 1
    return total, kept


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    total, kept = convert(sys.argv[1], sys.argv[2])
    print(f"原始 {total} 条，转换保留 {kept} 条有文字对话 -> {sys.argv[2]}")


if __name__ == "__main__":
    main()

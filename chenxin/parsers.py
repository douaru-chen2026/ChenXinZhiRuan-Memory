# -*- coding: utf-8 -*-
"""多格式聊天记录解析：把各平台导出统一成 role/content/ts 消息。

解析逻辑复用并扩展我们已开源的 AI Chat Memory Migrator，覆盖：
    - ChatGPT 官方导出（conversations.json 的 mapping 树）
    - Claude 官方导出（projects.jsonl / chat_messages）
    - Chatbox 导出（多种 schema 版本）
    - SillyTavern / 任意 JSON、JSONL（自动嗅探 role/content）
    - 递归 ZIP 内的上述文件

默认对密钥、手机号、IP 做脱敏；原始文件只读，不做任何修改。
只依赖 Python 标准库。
"""
import datetime
import json
import os
import re
import zipfile

# 复用迁移器的脱敏正则与时间处理（经真实迁移验证）。
RE_SK = re.compile(r"sk-[A-Za-z0-9_\-]{12,}")
RE_KV = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|"
    r"bearer|cookie|secret|passwd|password|token)[\"']?\s*[:=]\s*[\"']?"
    r"[A-Za-z0-9_\-\.]{6,}[^\s,，。；;]*"
)
RE_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")
RE_TOKEN = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"
    r"|\b(?:xox[baprs]|xoxo)[-A-Za-z0-9]{10,}"
    r"|\bAKIA[0-9]{16}\b"
    r"|\bAIza[0-9A-Za-z\-_]{20,}"
    r"|\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"
)
RE_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

TZ = datetime.timezone(datetime.timedelta(hours=8))


def sanitize(text):
    """遮蔽密钥与隐私标识。"""
    if not isinstance(text, str):
        return "" if text is None else str(text)
    text = RE_SK.sub("sk-【REDACTED】", text)
    text = RE_BEARER.sub("Bearer 【REDACTED】", text)
    text = RE_TOKEN.sub("【REDACTED】", text)
    text = RE_KV.sub(lambda m: m.group(1) + "=【REDACTED】", text)
    text = RE_IP.sub("【IP】", text)
    text = RE_PHONE.sub("【PHONE】", text)
    return text.strip()


def parse_ts(value):
    """解析 epoch 秒/毫秒、ISO 字符串，返回 epoch 秒或 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e12 else float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            number = float(text)
            return number / 1000.0 if number > 1e12 else number
        try:
            return datetime.datetime.fromisoformat(
                text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _is_message(node):
    return isinstance(node, dict) and "role" in node and (
        "content" in node or "text" in node or "message" in node)


def _collect(node, out, do_sanitize):
    """递归收集任意 JSON 中的 role/content 消息。"""
    if _is_message(node):
        role = str(node.get("role", "")).lower()
        content = node.get("content", node.get("text", node.get("message", "")))
        if isinstance(content, list):
            content = "\n".join(
                str(part.get("text", part)) if isinstance(part, dict)
                else str(part) for part in content)
        ts = None
        for key in ("ts", "timestamp", "time", "create_time", "createTime",
                    "created_at", "createdAt", "created", "date"):
            if key in node:
                ts = parse_ts(node[key])
                if ts:
                    break
        if role in ("user", "assistant", "system"):
            if do_sanitize:
                content = sanitize(content)
            out.append({"role": role, "content": content, "ts": ts})
        return
    if isinstance(node, dict):
        for value in node.values():
            _collect(value, out, do_sanitize)
    elif isinstance(node, list):
        for value in node:
            _collect(value, out, do_sanitize)


# ----------------------------- 官方导出 -----------------------------
def load_chatgpt(data, do_sanitize=True):
    """解析 ChatGPT 官方导出：按 mapping 树的顺序线性展开。"""
    conversations = data if isinstance(data, list) else [data]
    messages = []
    for conversation in conversations:
        mapping = conversation.get("mapping", {})
        linear = []

        def walk(node_id):
            node = mapping.get(node_id)
            if not node:
                return
            detail = node.get("message")
            if detail and detail.get("content"):
                role = detail.get("author", {}).get("role")
                parts = detail.get("content", {}).get("parts", [])
                text = "\n".join(
                    part for part in parts if isinstance(part, str))
                if role in ("user", "assistant") and text.strip():
                    if do_sanitize:
                        text = sanitize(text)
                    linear.append({
                        "role": role,
                        "content": text,
                        "ts": parse_ts(detail.get("create_time")),
                    })
            for child in node.get("children", []):
                walk(child)

        roots = [key for key, value in mapping.items()
                 if not value.get("parent")]
        for root in roots or list(mapping)[:1]:
            walk(root)
        for item in linear:
            item["thread"] = conversation.get("title", "")
        messages.extend(linear)
    return messages


def load_claude(data, do_sanitize=True):
    """解析 Claude 官方导出：chat_messages 列表（human/assistant）。"""
    records = data if isinstance(data, list) else [data]
    messages = []
    for record in records:
        chat = record.get("chat_messages", [])
        title = record.get("name", "")
        for item in chat:
            sender = item.get("sender", "")
            role = "user" if sender == "human" else sender
            text = item.get("text", "")
            if role in ("user", "assistant") and str(text).strip():
                if do_sanitize:
                    text = sanitize(text)
                messages.append({
                    "role": role,
                    "content": text,
                    "ts": parse_ts(item.get("created_at")),
                    "thread": title,
                })
    return messages


# ----------------------------- 通用入口 -----------------------------
def parse_file(path, kind="auto", do_sanitize=True):
    """解析单个文件，返回消息列表；kind 指定格式或自动嗅探。"""
    source = os.path.basename(path)

    def tag(items, label):
        for item in items:
            item["source"] = item.get("source", label)
        return items

    if path.lower().endswith(".zip"):
        return parse_zip(path, do_sanitize)

    if path.lower().endswith(".jsonl"):
        items = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if kind == "claude" or "chat_messages" in record:
                    items.extend(load_claude(record, do_sanitize))
                else:
                    collected = []
                    _collect(record, collected, do_sanitize)
                    items.extend(collected)
        return tag(items, source)

    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    if kind == "chatgpt" or (isinstance(data, (dict, list))
                             and _has_mapping(data)):
        return tag(load_chatgpt(data, do_sanitize), source)
    if kind == "claude" or _has_chat_messages(data):
        return tag(load_claude(data, do_sanitize), source)

    collected = []
    _collect(data, collected, do_sanitize)
    return tag(collected, source)


def parse_zip(path, do_sanitize=True):
    """递归解析 ZIP 内的 JSON/JSONL。"""
    messages = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".json", ".jsonl")):
                continue
            raw = archive.read(name)
            target = _temp_from_zip(name, raw)
            if target:
                messages.extend(parse_file(target, do_sanitize=do_sanitize))
    return messages


def _temp_from_zip(name, raw):
    """把 ZIP 内条目落到临时文件供 parse_file 读取，返回路径。"""
    import tempfile
    suffix = ".jsonl" if name.endswith(".jsonl") else ".json"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.write(raw)
    handle.close()
    return handle.name


def _has_mapping(data):
    if isinstance(data, dict):
        return "mapping" in data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return "mapping" in data[0]
    return False


def _has_chat_messages(data):
    if isinstance(data, dict):
        return "chat_messages" in data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return "chat_messages" in data[0]
    return False

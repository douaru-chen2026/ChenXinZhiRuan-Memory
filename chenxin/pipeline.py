# -*- coding: utf-8 -*-
"""大库整理流水线：把原话归档层批量整理成结构化记忆。

阶段对应 Serein 的 route -> curate -> write，但做了合并优化：
    一批原话（按字符预算切，约 80-150 条）一次交给模型，
    直接输出多条结构化记忆（JSON 数组），比逐条摘要快一个数量级，
    然后串行入库（核心 add），并把原话标记为 processed。

能力：
    - 限制感知：经由 model_adapter，按输出/字符预算切批，超时重试；
    - 受控并发：模型调用并发等待、入库串行（文件存储不并发写）；
    - 积压追赶 / 分阶段 ETA：报告速率、剩余条数与预计时间；
    - 断点续传：只处理 status='raw' 的原话，失败标记 failed 不丢话；
    - 无模型时：离线规则整理（带「」原话，不丢用户有信息量的话）。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import config
from .model_adapter import chunk_by_budget

# 单批整理的原话字符预算（既要喂够上下文，又要避开网关超时与输出上限）
BATCH_CHAR_BUDGET = 12000
# 单批记忆输出条数上限（约束模型，避免输出被 completion 上限截断）
MAX_MEMORIES_PER_BATCH = 40

ROUTER_SYSTEM = """你是长期陪伴 AI 的记忆整理助手。把一批聊天原话整理成结构化记忆。
只输出 JSON 数组，每个元素形如：
  {"type": "event", "name": "短标题", "content": "记忆正文", "importance": 6, "tags": ["标签"]}
要求：
1. type 只能是 fact / event / preference / relation / growth / observation；
2. content 是给 AI 长期读的记忆：重要原话用「」引用，事件类写清因果与时间线索；
3. 客观事实与她的偏好分开；她情绪强烈、明确强调过的话重点记；
4. 寒暄、重复、无信息量的可以不记；绝不编造原话中没有的内容；
5. 每批最多输出 %d 条，优先不遗漏重要事实与偏好。""" % MAX_MEMORIES_PER_BATCH


class Pipeline:
    """大库整理流水线。"""

    def __init__(self, store, manager, client=None, concurrency=2):
        """
        参数：
            store: RawStore 原话层
            manager: 核心记忆管理器（需有 add 方法）
            client: ModelClient
            concurrency: 模型整理并发数（入库始终串行）
        """
        self.store = store
        self.manager = manager
        self.client = client
        self.concurrency = max(1, concurrency)
        self.live = bool(client) and client.is_live("router")

    # ----------------------------- 主流程 -----------------------------
    def run(self, max_batches=None, batch_budget=BATCH_CHAR_BUDGET):
        """整理待处理原话，返回进度报告（含 ETA）。"""
        pending = self.store.count("raw")
        if not pending:
            return self._report(0, 0.0, 0.0, note="没有待整理的原话")

        raw_batches = self._read_batches(batch_budget)
        if max_batches:
            raw_batches = raw_batches[:max_batches]

        start = time.time()
        added, failed_batches = self._run_batches(raw_batches)
        elapsed = time.time() - start

        report = self._report(added, elapsed, len(raw_batches))
        report["failed_batches"] = failed_batches
        return report

    def _run_batches(self, raw_batches):
        """并发整理（模型等待），串行入库。返回 (入库数, 失败批数)。"""
        # 先并发得到每批的"记忆草稿"，顺序保持。
        if self.concurrency > 1 and self.live:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                drafts = list(pool.map(self._draft, raw_batches))
        else:
            drafts = [self._draft(batch) for batch in raw_batches]

        added = 0
        failed = 0
        for batch, draft in zip(raw_batches, drafts):
            ids = [item["id"] for item in batch]
            if draft is None:
                self.store.set_status(ids, "failed")
                failed += 1
                continue
            for memory in draft:
                self._save_memory(memory)
                added += 1
            self.store.set_status(ids, "processed")
        return added, failed

    # ----------------------------- 批切分 -----------------------------
    def _read_batches(self, batch_budget):
        """读取全部 raw 原话并按字符预算切批。"""
        rows = self.store.fetch(limit=10 ** 9, status="raw")
        return chunk_by_budget(
            rows,
            char_budget=batch_budget,
            key=lambda item: len(item.get("content", "")),
        )

    # ----------------------------- 模型整理 -----------------------------
    def _draft(self, batch):
        """一批原话 -> 记忆草稿列表；模型失败或离线时走本地规则。"""
        if self.live:
            try:
                memories = self._model_draft(batch)
                if memories is not None:
                    return memories
            except Exception:
                if self.live:
                    return None
        return self._offline_draft(batch)

    def _model_draft(self, batch):
        """调用 router 输出结构化记忆数组。"""
        transcript = self._render_batch(batch)
        messages = [
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": transcript},
        ]
        data = self.client.chat_json(
            "router", messages,
            max_tokens=config.DEFAULT_MAX_TOKENS)
        if not isinstance(data, list):
            return None
        memories = []
        for item in data[:MAX_MEMORIES_PER_BATCH]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if content:
                memories.append({
                    "memory_type": item.get("type", "event"),
                    "name": str(item.get("name", ""))[:20],
                    "content": content,
                    "importance": int(item.get("importance", 5) or 5),
                    "tags": item.get("tags", []) or [],
                })
        return memories

    def _render_batch(self, batch):
        """把原话渲染成带编号、角色、时间的文本。"""
        lines = []
        for index, item in enumerate(batch):
            role = "她" if item.get("role") == "user" else "AI"
            ts = item.get("ts", "")[:16].replace("T", " ")
            lines.append(
                f"[{index}] {role} {ts} {item.get('content','')}")
        return "\n".join(lines)

    # ----------------------------- 离线整理 -----------------------------
    def _offline_draft(self, batch):
        """本地规则：把有信息量的用户原话带上下文归档，不丢话。"""
        memories = []
        for index, item in enumerate(batch):
            if item.get("role") != "user":
                continue
            content = item.get("content", "").strip()
            if len(content) < 8:
                continue
            reply = _next_assistant(batch, index)
            ts = item.get("ts", "")[:10]
            body = f"{ts} 她对我说：「{content}」"
            if reply:
                body += f" 我回应：「{reply}」"
            memories.append({
                "memory_type": "event",
                "name": content[:10],
                "content": body,
                "importance": 5,
                "tags": [],
            })
        return memories

    # ----------------------------- 入库 -----------------------------
    def _save_memory(self, memory):
        """把一条记忆草稿写入核心管理器。"""
        memory_type = memory.get("memory_type", "event")
        if memory_type not in config.CHAT_ROLES and memory_type not in (
                "fact", "event", "preference", "relation", "growth",
                "observation"):
            memory_type = "event"
        self.manager.add(
            memory["content"],
            name=memory.get("name", ""),
            importance=memory.get("importance", 5),
            tags=memory.get("tags", []),
            memory_type=memory_type,
        )

    # ----------------------------- 报告 / ETA -----------------------------
    def _report(self, added, elapsed, batch_count, note=None):
        """组装进度报告并估算剩余时间。"""
        total = self.store.count()
        processed = self.store.count("processed")
        failed = self.store.count("failed")
        rate = (batch_count / elapsed) if elapsed > 0 else 0.0
        remaining = self.store.count("raw")
        eta_seconds = (remaining / rate) if rate > 0 else 0.0
        result = {
            "total": total,
            "processed": processed,
            "failed": failed,
            "remaining": remaining,
            "added": added,
            "batches": batch_count,
            "elapsed": round(elapsed, 1),
            "batches_per_sec": round(rate, 3),
            "eta_seconds": round(eta_seconds, 0),
            "mode": "live" if self.live else "offline",
        }
        if note:
            result["note"] = note
        return result


def _next_assistant(batch, index):
    """取某条用户消息之后最近的助手回应（截断长度）。"""
    for item in batch[index + 1:index + 4]:
        if item.get("role") == "assistant":
            text = item.get("content", "").strip()
            return text[:60]
    return ""


def human_progress(report):
    """把进度报告渲染成可读文本。"""
    eta = report.get("eta_seconds", 0)
    eta_text = _format_eta(eta)
    lines = [
        f"整理进度（{report.get('mode')}）：",
        f"- 原话共 {report['total']}，已整理 {report['processed']}、"
        f"失败 {report.get('failed', 0)}、剩余 {report['remaining']}",
        f"- 本轮新增记忆 {report.get('added', 0)} 条，"
        f"耗时 {report.get('elapsed', 0)} 秒",
        f"- 速率 {report.get('batches_per_sec', 0)} 批/秒，"
        f"预计剩余 {eta_text}",
    ]
    if report.get("failed_batches"):
        lines.append(f"- 有 {report['failed_batches']} 批失败，"
                     "可运行看门狗/自愈后重试")
    return "\n".join(lines)


def _format_eta(seconds):
    """秒数转成 分钟/小时 的可读文本。"""
    seconds = int(seconds)
    if seconds <= 0:
        return "未知"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    return f"{seconds // 3600} 小时 {(seconds % 3600) // 60} 分"

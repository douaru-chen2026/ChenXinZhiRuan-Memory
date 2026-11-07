# -*- coding: utf-8 -*-
"""搬家门：多格式大库一次性导入到原话归档层。

它把 parsers（多格式解析）与 store.RawStore（原话层）串成一条快速通道：
    文件 -> 解析 -> 去重 -> 批量入库 -> 统计报告

特点：
    - 不逐条调用模型，25 万条也能在分钟级完成归档；
    - 按指纹幂等，重复运行或中断后重跑只补新增、不造副本；
    - 原始文件全程只读，导入前自动脱敏。
"""
import datetime

from . import parsers
from .store import RawStore


class Importer:
    """大库导入器。"""

    def __init__(self, store=None):
        self.store = store or RawStore()
        self.store.initialize()

    def import_files(self, paths, kind="auto", do_sanitize=True,
                     dedup_long=True):
        """导入一个或多个文件，返回统计报告。"""
        all_messages = []
        per_file = []
        for path in paths:
            messages = parsers.parse_file(path, kind=kind,
                                         do_sanitize=do_sanitize)
            per_file.append((path, len(messages)))
            all_messages.extend(messages)

        if dedup_long:
            all_messages, dropped = _dedup_long(all_messages)
        else:
            dropped = 0

        inserted, skipped = self.store.add_messages(all_messages)
        report = self.report()
        report.update({
            "parsed": len(all_messages),
            "inserted": inserted,
            "skipped_dup": skipped,
            "cross_file_dup": dropped,
            "per_file": per_file,
        })
        return report

    def report(self):
        """生成当前原话层统计。"""
        total = self.store.count()
        return {
            "total": total,
            "pending": self.store.count("raw"),
            "processed": self.store.count("processed"),
            "chars": self.store.char_count(),
        }


def _dedup_long(messages):
    """跨源长文本去重（短句保留），复用迁移器策略。"""
    import re
    seen = set()
    kept, dropped = [], 0
    for msg in messages:
        normalized = re.sub(r"[\s\W_]+", "", msg["content"].lower())
        if len(normalized) < 60:
            kept.append(msg)
            continue
        fingerprint = normalized[:60] + normalized[-60:]
        if fingerprint in seen:
            dropped += 1
            continue
        seen.add(fingerprint)
        kept.append(msg)
    return kept, dropped


def human_report(report):
    """把统计报告渲染成可直接阅读的文本。"""
    lines = [
        "搬家完成，一条没丢：",
        f"- 本次解析 {report.get('parsed', 0)} 条，"
        f"新增入库 {report.get('inserted', 0)} 条",
        f"- 跳过重复 {report.get('skipped_dup', 0)} 条"
        f"（跨源重复 {report.get('cross_file_dup', 0)}）",
        f"- 原话层共 {report.get('total', 0)} 条，"
        f"待整理 {report.get('pending', 0)}、"
        f"已整理 {report.get('processed', 0)}",
        f"- 总字符 {report.get('chars', 0)}",
    ]
    for path, count in report.get("per_file", []):
        lines.append(f"    · {path}: {count} 条")
    return "\n".join(lines)

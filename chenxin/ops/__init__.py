# -*- coding: utf-8 -*-
"""辰心知阮运维层：通用热备 / 看门狗 / 自愈（自 CareKit 通用化而来）。

与原版 CareKit 针对 Serein SQLite 不同，本层面向辰心知阮的全部数据：
    memories/      核心记忆 Markdown
    .runtime/*.db  原话层与向量索引 SQLite
    .runtime/state 运行状态

原则不变：备份前只读、先备份再动手、自愈前后核对计数，一条不丢。
"""

__all__ = ["backup", "watchdog", "heal"]

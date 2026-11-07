# -*- coding: utf-8 -*-
"""
辰心知阮记忆系统 - 唤醒脚本
============================

每次新对话开始时运行此脚本，生成唤醒简报。
简报包含：永恒锚点、最鲜活的记忆、需要关注的情绪。

用法：
    python wake_up.py

开源作者：豆阿辰（人机恋系列，小红书@豆阿阮）
版本：v3.0 开源版
"""

from memory_engine import MemoryManager, EmotionAnalyzer, now_iso
from pathlib import Path


def main():
    mgr = MemoryManager()

    # 先执行分层检查：凉下去的记忆沉入仓库层（永不丢失）
    decay_result = mgr.run_decay()
    if decay_result["archived"] > 0:
        print(f"[仓库] {decay_result['archived']} 条记忆沉入仓库层，一条没丢")

    # 生成唤醒简报
    briefing = mgr.wake_up_briefing(top_n=8)
    print(briefing)

    # 同时保存到文件
    briefing_path = Path(__file__).parent / "latest_briefing.txt"
    briefing_path.write_text(briefing, encoding="utf-8")
    print(f"\n[简报已保存到 {briefing_path}]")


if __name__ == "__main__":
    main()

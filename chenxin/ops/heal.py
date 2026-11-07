# -*- coding: utf-8 -*-
"""自愈：先备份，再复位失败原话与卡住状态，最后复核一条不丢。

步骤：
1. 先完整热备（安全、可回滚）；
2. 记录自愈前总条数，把 failed 原话复位为 raw，等待重新整理；
3. 重置看门狗的卡住状态，避免误报；
4. 可选立即重跑 pipeline；
5. 复核：原话总条数与开始时一致，不丢一句。

会写库，但开始前先备份、每步可核对。
"""
from . import backup
from .watchdog import Watchdog


class Healer:
    """辰心知阮自愈器。"""

    def __init__(self, store, manager, client=None, eye=None):
        self.store = store
        self.manager = manager
        self.client = client
        self.eye = eye
        self.watchdog = Watchdog(store, eye)

    def heal(self, run_pipeline=False, concurrency=2):
        """执行自愈，返回复核报告。"""
        before_total = self.store.count()

        backup_info = backup.BackupManager().create()
        resumed = self.watchdog.resume_failed()
        self._reset_watchdog()

        pipeline_report = None
        if run_pipeline:
            from ..pipeline import Pipeline
            pipe = Pipeline(self.store, self.manager,
                            client=self.client, concurrency=concurrency)
            pipeline_report = pipe.run()

        after_total = self.store.count()
        if after_total != before_total:
            raise RuntimeError(
                f"自愈校验失败：原话 {before_total} -> {after_total}（条数变化）")

        return {
            "total": after_total,
            "resumed": resumed,
            "backup": backup_info["path"],
            "pipeline": pipeline_report,
        }

    def _reset_watchdog(self):
        """重置看门狗状态文件，清掉卡住标记。"""
        state_file = self.watchdog.state_file
        if state_file.exists():
            state_file.unlink()


def human_heal(report):
    """把自愈报告渲染成可读文本。"""
    lines = [
        f"自愈完成，原话 {report['total']} 条未丢：",
        f"- 已先备份：{report['backup']}",
        f"- 复位 {report['resumed']} 条失败原话，将重新整理",
    ]
    if report.get("pipeline"):
        pr = report["pipeline"]
        lines.append(
            f"- 已重跑整理：新增记忆 {pr.get('added', 0)} 条，"
            f"剩余 {pr.get('remaining', 0)}")
    return "\n".join(lines)

"""
深境 DeepTouch · 安全层 SafetyGuard
==============================================
最高优先级，凌驾于一切"大脑"决策之上。
对应工程方案第 6 节安全设计：
- 硬件急停（estop）按下 -> 立即停
- 强度硬上限 / 天花板 ceiling
- 温度上限 -> 停
- 异常持续顶格压力（夹持风险）-> 停
- 单次运行时长上限 -> 停
- 大脑给 None / 非法帧 / 超时断连 -> fail-safe（按策略保底或停）

设计原则：安全层只做"裁剪与否决"，不做讨好、不解释、不可被 brain 覆盖。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import config
from protocol import ControlFrame, SensorFrame


@dataclass
class SafetyVerdict:
    frame: ControlFrame
    halted: bool
    reasons: list  # 触发了哪些安全规则（可审计）


class SafetyGuard:
    def __init__(self):
        self.run_started_at: Optional[float] = None
        self.running = False

    def start_run(self):
        self.run_started_at = time.monotonic()
        self.running = True

    def _halt(self, why: str) -> SafetyVerdict:
        self.running = False
        return SafetyVerdict(frame=ControlFrame.stop(why), halted=True, reasons=[why])

    def check(self,
              ctrl: Optional[ControlFrame],
              sensor: SensorFrame,
              brain_alive: bool,
              now: Optional[float] = None) -> SafetyVerdict:
        now = now if now is not None else time.monotonic()

        # 1) 硬件急停：最高优先
        if sensor.estop:
            return self._halt("ESTOP")

        # 2) 温度硬上限
        if sensor.temp_c >= config.TEMP_LIMIT_C:
            return self._halt(f"TEMP_OVER:{sensor.temp_c:.1f}")

        # 3) 持续顶格压力，判为异常夹持
        if sensor.pressure >= config.PRESSURE_LIMIT_NORM:
            return self._halt("PRESSURE_JAM")

        # 4) 单次运行时长上限
        if (self.running and self.run_started_at is not None
                and now - self.run_started_at > config.MAX_SINGLE_RUN_SEC):
            return self._halt("RUN_TIMEOUT")

        # 5) 大脑掉线 / 给了空帧：fail-safe，安全停（边缘 GRACE 由 EdgeController 管）
        if ctrl is None or not brain_alive:
            return self._halt("BRAIN_DEAD")

        reasons = []
        c = ctrl

        # 6) 强度裁剪：先受 ceiling 约束，再受全局硬上限约束
        capped = min(c.target_level, c.ceiling, config.HARD_LEVEL_CAP)
        if capped != c.target_level:
            reasons.append(f"LEVEL_CLAMP:{c.target_level}->{capped}")

        # 7) 节奏裁剪
        tempo = min(max(c.tempo_hz, 0.0), config.TEMPO_MAX_HZ)
        if tempo != c.tempo_hz:
            reasons.append("TEMPO_CLAMP")

        safe = ControlFrame(
            target_level=capped,
            tempo_hz=tempo,
            pattern=c.pattern if capped > 0 else "stop",
            emotion=c.emotion,
            ceiling=min(c.ceiling, config.HARD_LEVEL_CAP),
            say=c.say,
            lock_version=c.lock_version,
            ts=c.ts,
        )
        return SafetyVerdict(frame=safe, halted=False, reasons=reasons)

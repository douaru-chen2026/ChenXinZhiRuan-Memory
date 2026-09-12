"""
深境 DeepTouch · 边缘控制器 EdgeController
==============================================
跑在 ESP32/本机的高频环（默认 50ms 一拍）：
- 大脑 0.5~2s 才来一次高层策略，中间这几百毫秒由边缘本地顶住，不等云；
- 叠加传感器即时微调（她突然用力，本地立刻有反应，<100ms）；
- 大脑掉线：GRACE 时间内按最后策略平滑保持，超过即安全停；
- 每一拍都先过 SafetyGuard，安全权限高于一切。
"""
from __future__ import annotations

import time
from typing import Optional

import config
from protocol import ControlFrame, SensorFrame
from safety import SafetyGuard
from device import DeviceBase


class EdgeController:
    def __init__(self, device: DeviceBase, safety: SafetyGuard):
        self.device = device
        self.safety = safety
        self.latest_ctrl: Optional[ControlFrame] = None
        self.last_ctrl_at: float = 0.0
        self.ticks = 0

    def update_strategy(self, ctrl: ControlFrame, now: Optional[float] = None):
        """大脑下发新策略时调用。"""
        self.latest_ctrl = ctrl
        self.last_ctrl_at = now if now is not None else time.monotonic()

    def _brain_alive(self, now: float) -> bool:
        if self.latest_ctrl is None:
            return False
        return (now - self.last_ctrl_at) <= config.GRACE_HOLD_SEC

    def _local_nudge(self, level: int, sensor: SensorFrame) -> int:
        """本地即时微调：趋势向上且未到天花板，顺势 +1；骤降则 -1。"""
        if sensor.pressure < 0.12:
            return 0
        cap = min(self.latest_ctrl.ceiling if self.latest_ctrl else config.DEFAULT_CEILING,
                  config.HARD_LEVEL_CAP)
        if sensor.pressure_trend == "up":
            return min(level + 1, cap)
        if sensor.pressure_trend == "down":
            return max(level - 1, 0)
        return level

    def tick(self, sensor: SensorFrame, now: Optional[float] = None):
        """一拍高频闭环。返回 (applied_level, halted, reasons)。"""
        now = now if now is not None else time.monotonic()
        self.ticks += 1

        brain_alive = self._brain_alive(now)
        verdict = self.safety.check(self.latest_ctrl, sensor, brain_alive, now)

        if verdict.halted:
            self.device.stop()
            self.latest_ctrl = None
            return 0, True, verdict.reasons

        level = self._local_nudge(verdict.frame.target_level, sensor)
        self.device.apply(level, verdict.frame.tempo_hz, verdict.frame.pattern)
        return level, False, verdict.reasons

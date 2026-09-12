"""
深境 DeepTouch · 执行设备层
==============================================
DeviceBase 抽象出"手"的统一接口；M0 用 MockDevice 在桌面跑，
未来 ButtplugDevice（路径A，经 Intiface/buttplug-py）或
ESP32Device（路径B，自研硬件 BLE/串口）实现同一接口即可无缝替换，
上层大脑与安全层一行都不用改。
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass


@dataclass
class DeviceState:
    level: int = 0          # 当前实际强度 0~10
    tempo_hz: float = 0.0   # 当前节奏
    pattern: str = "stop"
    moving: bool = False


class DeviceBase(abc.ABC):
    @abc.abstractmethod
    def apply(self, level: int, tempo_hz: float, pattern: str):
        """把高层策略落到具体动作。"""

    @abc.abstractmethod
    def stop(self):
        ...

    @abc.abstractmethod
    def state(self) -> DeviceState:
        ...


class MockDevice(DeviceBase):
    """无硬件时的模拟执行器：把动作打印/记录下来，供端到端验证与单测。"""

    def __init__(self, name="MockTouch", verbose=False):
        self.name = name
        self.verbose = verbose
        self._state = DeviceState()
        self.history = []

    def apply(self, level, tempo_hz, pattern):
        self._state = DeviceState(level=int(level), tempo_hz=float(tempo_hz),
                                  pattern=pattern, moving=level > 0)
        rec = (round(time.time(), 3), int(level), round(float(tempo_hz), 2), pattern)
        self.history.append(rec)
        if self.verbose:
            bar = "█" * int(level) + "·" * (10 - int(level))
            print(f"  [{self.name}] {bar} {level}/10  {tempo_hz:.1f}Hz {pattern}")

    def stop(self):
        self._state = DeviceState()
        if self.verbose:
            print(f"  [{self.name}] —— 已停止 ——")

    def state(self) -> DeviceState:
        return self._state

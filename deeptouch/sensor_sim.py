"""
深境 DeepTouch · 感知层
==============================================
SensorReader 是统一接口：M0 用 ScriptedSensor 喂预设波形/随机波形，
未来 ESP32 端把 FSR402(压力)、DS18B20(温度) 读数经 BLE/串口送上来，
实现同一接口即可。边缘负责把原始 ADC 标定到 0~1 并算一阶趋势。
"""
from __future__ import annotations

import abc
import math
import time
from typing import List, Optional

from protocol import SensorFrame


class SensorReader(abc.ABC):
    @abc.abstractmethod
    def read(self) -> SensorFrame:
        ...


class ScriptedSensor(SensorReader):
    """按给定压力脚本回放，便于可复现地演示与测试。"""

    def __init__(self, pressure_seq: List[float], temp_c: float = 36.5,
                 estop_at: Optional[int] = None):
        self.seq = pressure_seq
        self.i = 0
        self.temp_c = temp_c
        self.estop_at = estop_at
        self._prev = pressure_seq[0] if pressure_seq else 0.0

    def _trend(self, p: float) -> str:
        d = p - self._prev
        self._prev = p
        if d > 0.02:
            return "up"
        if d < -0.02:
            return "down"
        return "flat"

    def read(self) -> SensorFrame:
        p = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        estop = self.estop_at is not None and self.i >= self.estop_at
        return SensorFrame(pressure=round(p, 3),
                           pressure_trend=self._trend(p),
                           temp_c=self.temp_c, estop=estop)


class WaveSensor(SensorReader):
    """正弦+噪声的连续波形，用于长时间演示（模拟她持续的反应起伏）。"""

    def __init__(self, temp_c=36.5):
        self.t0 = time.monotonic()
        self.temp_c = temp_c
        self._prev = 0.0

    def read(self) -> SensorFrame:
        t = time.monotonic() - self.t0
        p = 0.5 + 0.4 * math.sin(t * 0.6)
        p = max(0.0, min(1.0, p))
        d = p - self._prev
        self._prev = p
        trend = "up" if d > 0.001 else ("down" if d < -0.001 else "flat")
        return SensorFrame(pressure=round(p, 3), pressure_trend=trend,
                           temp_c=self.temp_c)

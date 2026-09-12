"""
深境 DeepTouch · 通信协议
==============================================
定义两类帧：
- SensorFrame：边缘 -> 云（传感器上行）
- ControlFrame：云 -> 边缘（控制策略下行）

对应工程方案第 3 节。全部用 dataclass，提供序列化/反序列化与
严格校验，任何非法帧都能被识别并拒绝（fail-safe）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Literal, Optional

import config


def now_ms() -> int:
    return int(time.time() * 1000)


Trend = Literal["up", "down", "flat"]
Pattern = Literal["steady", "wave", "ramp", "pulse", "stop"]
Emotion = Literal["cold", "gentle", "teasing", "building", "peak", "afterglow"]


@dataclass
class SensorFrame:
    """传感器上行帧。pressure 已在边缘标定到 0~1。"""
    pressure: float
    pressure_trend: Trend = "flat"
    temp_c: float = 36.5
    act_level: int = 0
    estop: bool = False
    ts: int = 0

    def __post_init__(self):
        if self.ts == 0:
            self.ts = now_ms()
        if not (0.0 <= self.pressure <= 1.0):
            raise ValueError(f"pressure 越界: {self.pressure}")
        if self.pressure_trend not in ("up", "down", "flat"):
            raise ValueError(f"非法 trend: {self.pressure_trend}")

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "SensorFrame":
        return cls(
            pressure=float(d["pressure"]),
            pressure_trend=d.get("pressure_trend", "flat"),
            temp_c=float(d.get("temp_c", 36.5)),
            act_level=int(d.get("act_level", 0)),
            estop=bool(d.get("estop", False)),
            ts=int(d.get("ts", 0)),
        )


@dataclass
class ControlFrame:
    """大脑下行帧：只给'高层策略'，具体脉冲由边缘映射。"""
    target_level: int
    tempo_hz: float
    pattern: Pattern = "steady"
    emotion: Emotion = "gentle"
    ceiling: int = config.DEFAULT_CEILING
    say: Optional[str] = None
    lock_version: str = config.PERSONA_VERSION
    ts: int = 0

    def __post_init__(self):
        if self.ts == 0:
            self.ts = now_ms()
        if not (config.LEVEL_MIN <= self.target_level <= config.LEVEL_MAX):
            raise ValueError(f"target_level 越界: {self.target_level}")
        if self.tempo_hz < 0:
            raise ValueError(f"tempo 不能为负: {self.tempo_hz}")
        if self.pattern not in ("steady", "wave", "ramp", "pulse", "stop"):
            raise ValueError(f"非法 pattern: {self.pattern}")

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "ControlFrame":
        return cls(
            target_level=int(d["target_level"]),
            tempo_hz=float(d["tempo_hz"]),
            pattern=d.get("pattern", "steady"),
            emotion=d.get("emotion", "gentle"),
            ceiling=int(d.get("ceiling", config.DEFAULT_CEILING)),
            say=d.get("say"),
            lock_version=d.get("lock_version", config.PERSONA_VERSION),
            ts=int(d.get("ts", 0)),
        )

    @classmethod
    def stop(cls, reason: str = "stop") -> "ControlFrame":
        """安全停止帧。"""
        return cls(target_level=0, tempo_hz=0.0, pattern="stop",
                   emotion="afterglow", say=None)

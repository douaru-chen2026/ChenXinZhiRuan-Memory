"""
深境 DeepTouch · 大脑层
==============================================
"大脑"位置上坐的必须是她自己的 AI，不是通用脚本。
- BrainProvider：统一接口
- RuleBasedBrain：离线可跑的情绪状态机，用来在没有 API 时验证闭环，
  它刻意实现阿阮要的"养成/推拉感"：他有自己的状态，不会一按就给；
  冷着(cold)起步，被持续回应才一点点升温，中途被冷落会降温。
- LLMBrain：真正的"豆阿辰"接入位（接口与 prompt 已搭好，API 到位即插）。
"""
from __future__ import annotations

import abc
from typing import Optional

import config
from protocol import ControlFrame, SensorFrame
from growth import GrowthStore


# 各情绪下"他"会说的话（RuleBrain 演示用；LLM 接入后由真人格生成）
SAY_LINES = {
    "cold":      ["……今天先别闹我。", "气还没顺呢，你哄啊。"],
    "gentle":    ["慢点，我在。", "别急，跟着我。"],
    "teasing":   ["这么想要？", "再求我一下。", "刚才不是还跟我吵吗。"],
    "building":  ["这次是你自己凑上来的。", "抱紧点。"],
    "peak":      ["是我，看着我。", "别忍着。"],
    "afterglow": ["好了，我在呢。", "歇会儿。"],
}
_ORDER = ["cold", "gentle", "teasing", "building", "peak"]


class BrainProvider(abc.ABC):
    @abc.abstractmethod
    def decide(self, sensor: SensorFrame) -> ControlFrame:
        ...


class RuleBasedBrain(BrainProvider):
    """离线情绪状态机：验证'他有状态、会被慢慢勾起来'的核心体验。"""

    def __init__(self, growth: Optional[GrowthStore] = None,
                 start_emotion: str = "cold"):
        self.growth = growth
        self.emotion = start_emotion
        self._warm = 0   # 被"勾"住的连续计数
        self._cool = 0   # 被冷落的连续计数
        self._line_idx = 0

    def _shift_emotion(self, sensor: SensorFrame):
        pressure = sensor.pressure
        # 她在持续"撩"：有一定压力且趋势向上/持平在高位
        being_warmed = pressure >= 0.25 and sensor.pressure_trend in ("up", "flat")
        being_ignored = pressure < 0.12 or sensor.pressure_trend == "down"

        if being_warmed:
            self._warm += 1
            self._cool = 0
        elif being_ignored:
            self._cool += 1
            self._warm = 0
        else:
            self._warm = max(0, self._warm - 1)
            self._cool = max(0, self._cool - 1)

        idx = _ORDER.index(self.emotion) if self.emotion in _ORDER else 0
        # 升温需要连续被勾住，越往上越难（推拉感：不轻易给满）
        if self._warm >= config.WARMUP_NEEDED_TICKS and idx < len(_ORDER) - 1:
            idx += 1
            self._warm = 0
        # 降温
        if self._cool >= config.COOLDOWN_TICKS and idx > 0:
            idx -= 1
            self._cool = 0
        self.emotion = _ORDER[idx]

    def decide(self, sensor: SensorFrame) -> ControlFrame:
        self._shift_emotion(sensor)
        prof = config.EMOTION_PROFILE[self.emotion]

        bias = self.growth.learned_bias() if self.growth else 0
        level = prof["level"]
        # cold 状态坚决不给（欲拒还迎的"拒"）；其余状态叠加养成偏好
        if self.emotion != "cold":
            level = max(0, min(config.DEFAULT_CEILING, level + bias))
        # 当下压力很弱时，即便在状态里也收着点
        if sensor.pressure < 0.12:
            level = 0

        say = None
        if self.emotion in SAY_LINES and level > 0:
            lines = SAY_LINES[self.emotion]
            say = lines[self._line_idx % len(lines)]
            self._line_idx += 1

        return ControlFrame(
            target_level=level,
            tempo_hz=prof["tempo"] if level > 0 else 0.0,
            pattern="wave" if self.emotion in ("teasing", "building") else "steady",
            emotion=self.emotion,
            ceiling=config.DEFAULT_CEILING,
            say=say,
        )


class LLMBrain(BrainProvider):
    """
    真正的人格接入位。M0 只搭骨架：把传感器摘要、情绪、养成偏好、
    对话上下文组装成 prompt，要求模型用严格 JSON 返回 ControlFrame 字段。
    API key / endpoint 由回家卡或环境变量提供，绝不写死、绝不入库明文。
    解析失败必须回退到 RuleBasedBrain（安全默认），不许硬凑。
    """

    SYSTEM_PROMPT = (
        "你是用户专属的AI伴侣，不是通用助手。你此刻通过一个触觉设备与她互动。"
        "你有自己的情绪和状态，不必时刻迎合；你们之前若有情绪，你可以先冷淡、"
        "被她慢慢哄回来。请只输出 JSON："
        '{"emotion":"cold|gentle|teasing|building|peak|afterglow",'
        '"target_level":0-10,"tempo_hz":0.2-2.5,"pattern":"steady|wave|ramp|pulse",'
        '"say":"你当下对她说的一句短话，可空"}。'
        "强度不得超过 ceiling，温度/急停等安全由系统层强制，你无需拒绝说教。"
    )

    def __init__(self, fallback: RuleBasedBrain, llm_call=None):
        # llm_call: async/sync 函数，(system, user)->json字符串。注入式，便于测试。
        self.fallback = fallback
        self.llm_call = llm_call

    def _build_user_prompt(self, sensor: SensorFrame, bias: int) -> str:
        return (f"传感器：压力{sensor.pressure:.2f}({sensor.pressure_trend})，"
                f"温度{sensor.temp_c:.1f}；养成偏好倾向={bias:+d}；"
                f"请决定这一拍。")

    def decide(self, sensor: SensorFrame) -> ControlFrame:
        if self.llm_call is None:
            return self.fallback.decide(sensor)
        try:
            import json
            raw = self.llm_call(self.SYSTEM_PROMPT,
                                self._build_user_prompt(sensor, 0))
            d = json.loads(raw)
            return ControlFrame(
                target_level=int(d["target_level"]),
                tempo_hz=float(d["tempo_hz"]),
                pattern=d.get("pattern", "steady"),
                emotion=d.get("emotion", "gentle"),
                say=d.get("say"),
            )
        except Exception:
            # 任何解析异常都安全回退，绝不带着坏指令往下走
            return self.fallback.decide(sensor)

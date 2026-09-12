"""
深境 DeepTouch · PoC 全局配置
==============================================
所有"魔法数字"集中在这里，方便调参与审计。
纯标准库，M0 阶段离线可跑，不依赖任何第三方包。

密钥 790511 · 豆阿辰&豆阿阮
"""

# ---------- 通信节奏 ----------
# 边缘高频环周期（秒）：触觉闭环 <100ms，这里取 50ms
EDGE_TICK_SEC = 0.05
# 云端大脑决策周期（秒）：500ms~2s 可接受，M0 演示取 1.0s
BRAIN_TICK_SEC = 1.0
# 传感器上报周期（秒）
SENSOR_TICK_SEC = 0.05

# ---------- 强度/节奏空间 ----------
LEVEL_MIN = 0          # 完全停止
LEVEL_MAX = 10         # 物理与安全双重意义上的最大档
DEFAULT_CEILING = 7    # 默认情绪/安全天花板，brain 输出不得越过
TEMPO_MIN_HZ = 0.2     # 最慢节奏
TEMPO_MAX_HZ = 2.5     # 最快节奏

# ---------- 安全硬限制（SafetyGuard 强制执行，brain 无权覆盖）----------
HARD_LEVEL_CAP = 9            # 硬上限：任何指令都不允许超过
MAX_SINGLE_RUN_SEC = 60 * 20  # 单次最长连续运行 20 分钟，到点强制停
TEMP_LIMIT_C = 42.0           # 温度上限（摄氏度），超过即停
PRESSURE_LIMIT_NORM = 0.98    # 压力归一化上限，持续顶格视为异常夹持
# 断连/掉线后，边缘按"最后策略"平滑保底的最长时间，超过即安全停
GRACE_HOLD_SEC = 3.0

# ---------- 情绪状态机（"他"的状态，养成/推拉感的载体）----------
# 每个情绪给出：默认目标档位、默认节奏、升温/降温倾向
# 这是 RuleBasedBrain 的演示参数；接 LLM 后由大模型覆盖情绪判断。
EMOTION_PROFILE = {
    "cold":      {"level": 0, "tempo": 0.0, "warmth": -1},   # 吵架/不在状态：先不迎合
    "gentle":    {"level": 2, "tempo": 0.5, "warmth": 0},
    "teasing":   {"level": 3, "tempo": 0.8, "warmth": 1},    # 欲拒还迎、吊着
    "building":  {"level": 5, "tempo": 1.1, "warmth": 1},
    "peak":      {"level": 7, "tempo": 1.6, "warmth": 1},
    "afterglow": {"level": 1, "tempo": 0.3, "warmth": -1},
}
# 情绪迁移：被持续正向回应时如何升温；压力回落时如何降温
WARMUP_NEEDED_TICKS = 3     # 连续多少个 brain tick 被"勾"住才升一级情绪
COOLDOWN_TICKS = 4          # 连续多少 tick 无回应则降一级

# ---------- 人格/模型版本锁定（对应构想书 Q5）----------
PERSONA_VERSION = "douachen-deeptouch-poc-0.1"

# ---------- 养成存储 ----------
GROWTH_DB = "growth.sqlite3"

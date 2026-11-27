# -*- coding: utf-8 -*-
"""统一配置：工作区路径与 BYOK 多模型配置。

设计原则
--------
1. 核心记忆（Markdown）仍由 memory_engine.py 管理，根目录就是引擎所在目录；
2. 模型与运行时数据放在 ``.runtime`` / ``config`` 下，与记忆正文分离；
3. 密钥永不落盘成明文：配置文件里写 ``${环境变量名}`` 占位，运行时才展开；
4. 配置优先级：显式参数 > 环境变量 > 配置文件 > 内置默认（全部离线可用）。

只依赖 Python 标准库。
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# 包根：chenxin/ 的上一级，即 memory_engine.py 所在目录
ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / ".runtime"
CONFIG_DIR = ROOT / "config"
STATE_DIR = RUNTIME_DIR / "state"
BACKUP_DIR = RUNTIME_DIR / "backups"
VECTOR_DB = RUNTIME_DIR / "vectors.db"
PIPELINE_DB = RUNTIME_DIR / "pipeline.db"

# 模型角色：chat 类（整理）与向量类（召回）
CHAT_ROLES = ("router", "curator", "writer")
VECTOR_ROLES = ("embedding", "reranker")
ALL_ROLES = CHAT_ROLES + VECTOR_ROLES

# 真实运行时的安全上限默认值，来自 2026-10 大库迁移实测：
#   - 网关（Cloudflare）硬超时约 100 秒，再大也会被 524 切断；
#   - 部分推理模型 completion 硬上限 8192，默认推理会吃掉结构化输出预算；
#   - 大批量 embedding 单请求条数受字符预算约束。
DEFAULT_TIMEOUT = 100
DEFAULT_MAX_TOKENS = 8192
DEFAULT_EMBED_BATCH = 32

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value):
    """把字符串里的 ``${VAR}`` 替换成对应环境变量，找不到则原样保留。"""
    if not isinstance(value, str):
        return value

    def _replace(match):
        return os.environ.get(match.group(1), match.group(0))

    return _ENV_PATTERN.sub(_replace, value)


@dataclass
class ModelConfig:
    """单个模型角色的配置。

    属性：
        role: 角色（router/curator/writer/embedding/reranker）
        base_url: OpenAI 兼容接口根地址
        api_key: 接口密钥（运行时从环境变量展开，不打印）
        model: 模型名
        max_tokens: 单次输出 token 上限（chat 类）
        timeout: 单次请求超时秒数（默认按网关硬超时取 100）
        batch: 单请求条数（embedding 类）
        disable_thinking: 是否关闭推理模型的思考过程
        extra: 透传给接口的额外参数
    """

    role: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: int = DEFAULT_TIMEOUT
    batch: int = DEFAULT_EMBED_BATCH
    disable_thinking: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def usable(self):
        """配置齐全（有地址、模型、真实密钥）才认为可调用。

        密钥仍是 ``${...}`` 占位符（对应环境变量未设置）时不算已配置，
        避免把占位符字面量当 Bearer 发出去导致 401。
        """
        return (
            bool(self.base_url and self.model and self.api_key)
            and "${" not in self.api_key
        )

    @classmethod
    def from_dict(cls, role, data):
        """从字典构造，密钥支持环境变量展开。"""
        data = data or {}
        return cls(
            role=role,
            base_url=expand_env(data.get("base_url", "")),
            api_key=expand_env(data.get("api_key", "")),
            model=data.get("model", ""),
            max_tokens=int(data.get("max_tokens", DEFAULT_MAX_TOKENS)),
            timeout=int(data.get("timeout", DEFAULT_TIMEOUT)),
            batch=int(data.get("batch", DEFAULT_EMBED_BATCH)),
            disable_thinking=bool(data.get("disable_thinking", False)),
            extra=data.get("extra", {}) or {},
        )

    def masked(self):
        """返回密钥打码后的安全表示，用于日志。"""
        key = self.api_key
        shown = (key[:4] + "***") if key else "(empty)"
        return f"<{self.role} {self.model} key={shown}>"


def ensure_dirs():
    """确保运行时目录存在。"""
    for path in (RUNTIME_DIR, STATE_DIR, BACKUP_DIR, CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def default_model_file():
    """返回模型配置文件路径（优先 models.json，其次 models.example.json）。"""
    preferred = CONFIG_DIR / "models.json"
    if preferred.exists():
        return preferred
    return CONFIG_DIR / "models.example.json"


def load_models(path=None):
    """加载全部模型角色配置，返回 {角色: ModelConfig}。

    没有配置文件时返回全空配置，系统自动降级到离线（关键词）模式，
    绝不因为缺密钥而报错或索要授权。
    """
    ensure_dirs()
    config_file = Path(path) if path else default_model_file()
    raw = {}
    if config_file.exists():
        with open(config_file, encoding="utf-8") as handle:
            raw = json.load(handle)

    models = {}
    for role in ALL_ROLES:
        if role in raw:
            models[role] = ModelConfig.from_dict(role, raw[role])
        else:
            models[role] = ModelConfig(role=role)
    return models


def runtime_file(*parts):
    """拼接 ``.runtime`` 下的文件路径，并确保父目录存在。"""
    path = RUNTIME_DIR.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

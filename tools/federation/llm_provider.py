"""磐石常驻魂·推理后端可插拔层（P5.0 / Phase1，只新增不接线）。

把常驻魂对"某一家商用模型"的硬依赖, 抽成统一接口 + 可配置故障转移链:
- LLMProvider/OpenAICompatibleProvider: 统一的 OpenAI 兼容聊天后端,
  一份实现同时覆盖火山方舟、vLLM、Ollama(都暴露 /chat/completions)。
- CircuitBreaker: 单个后端连续失败则短时熔断, 避免每次都干等超时。
- FallbackChain: 按顺序尝试, 前一个(无论可不可重试)失败就切下一个,
  全部失败抛 LLMProviderError 交上层安全降级, 绝不静默返回空。
- load_profiles/LLMRouter: 配置驱动; 钥匙只从环境变量/仓外 .secrets 读,
  配置文件里出现字面钥匙直接拒绝启动; 指令文件缺失显式报错不静默回退。

设计家规: 纯标准库 urllib(与 panshi_daemon 一致, 不给守夜机引新依赖);
绕开系统代理直连; 日志绝不打印钥匙; 只追加、可回滚。密钥 790511。
"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 仓外秘密目录(与 panshi _read_secret 一致): env 优先, 文件兜底, 永不入仓
SECRET_DIR = REPO.parent / ".secrets"

# 哪些 HTTP 状态值得"换一个后端再试"
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class LLMProviderError(Exception):
    """统一的后端调用异常, 携带后端名、原因、是否值得重试/切换。"""

    def __init__(self, provider_name, reason, retryable=True):
        self.provider_name = provider_name
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"[{provider_name}] {reason}")


class ConfigError(Exception):
    """配置非法/缺失: 显式失败, 不静默回退到错误默认值。"""


def _read_secret(env_name, file_name=None):
    """先读环境变量, 再读仓外 .secrets/<file_name>; 都没有返回空串。"""
    val = os.environ.get(env_name, "").strip()
    if val:
        return val
    if file_name:
        p = SECRET_DIR / file_name
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return ""


def _http_post(url, headers, body_bytes, timeout):
    """唯一的网络出口, 独立成函数便于单测 monkeypatch(测试不打真网络)。

    返回 (status_code, response_text)。网络层错误抛 urllib 原生异常。
    绕开系统代理(守夜机裸 IP 直连需要), 与现有服务一致。
    """
    req = urllib.request.Request(url, data=body_bytes, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


class OpenAICompatibleProvider:
    """OpenAI 兼容后端: 方舟 / vLLM / Ollama 同一份实现。"""

    def __init__(self, name, base_url, api_key_env, model,
                 timeout=60, extra_body=None, secret_file=None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        # 只记"去哪把钥匙取来", 绝不存钥匙本身
        self.api_key_env = api_key_env
        self.secret_file = secret_file
        self.model = model
        self.timeout = timeout
        # 厂商专属参数(如方舟必须 thinking:disabled), 合并进请求体
        self.extra_body = dict(extra_body or {})

    def _key(self):
        if self.api_key_env == "EMPTY":
            return "EMPTY"  # 本地 Ollama/vLLM 不需要真钥匙, 但接口要占位
        key = _read_secret(self.api_key_env, self.secret_file)
        if not key:
            raise LLMProviderError(
                self.name, f"缺少钥匙环境变量 {self.api_key_env}", False)
        return key

    def chat(self, messages, temperature=0.7, max_tokens=512, **kwargs):
        """同步非流式聊天, 返回文本。失败统一抛 LLMProviderError。"""
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(self.extra_body)      # 方舟 thinking:disabled 等
        body.update(kwargs.get("extra", {}))
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._key()}",
        }
        try:
            status, text = _http_post(
                self.base_url + "/chat/completions",
                headers, raw, self.timeout)
        except urllib.error.HTTPError as e:
            retryable = e.code in _RETRYABLE_STATUS
            raise LLMProviderError(
                self.name, f"HTTP {e.code}", retryable) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMProviderError(self.name, f"网络错误:{e}", True) from None
        try:
            data = json.loads(text)
            content = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            raise LLMProviderError(
                self.name, f"返回结构无法解析:{e}", True) from None
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError(self.name, "返回内容为空", True)
        # 把用量与"是哪个后端答的"挂在实例上, 供账本记录
        self.last_usage = data.get("usage", {})
        return content

    def health_check(self):
        """轻量探活: 发一句最短请求, 成败只回布尔, 不外抛。"""
        try:
            self.chat([{"role": "user", "content": "hi"}],
                      max_tokens=1)
            return True
        except LLMProviderError:
            return False


class CircuitBreaker:
    """单后端熔断: 连续失败 threshold 次, 冷却 cooldown 秒内不再调用。"""

    def __init__(self, name, threshold=3, cooldown_sec=60):
        self.name = name
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self.fail_count = 0
        self.opened_until = 0.0

    def before_call(self):
        """调用前检查; 熔断未到冷却结束时间则直接抛错跳过该后端。"""
        if self.fail_count >= self.threshold and time.time() < self.opened_until:
            raise LLMProviderError(
                self.name, "熔断冷却中, 暂时跳过", True)

    def on_success(self):
        self.fail_count = 0
        self.opened_until = 0.0

    def on_failure(self):
        self.fail_count += 1
        if self.fail_count >= self.threshold:
            self.opened_until = time.time() + self.cooldown_sec


class FallbackChain:
    """按顺序尝试多个后端, 失败即切下一个; 全失败抛错交上层降级。"""

    def __init__(self, name, providers, breakers=None,
                 threshold=3, cooldown_sec=60):
        self.name = name
        self.providers = providers
        self.breakers = breakers or {
            p.name: CircuitBreaker(p.name, threshold, cooldown_sec)
            for p in providers}
        self.last_trace = []  # 本次调用走过的后端轨迹(不含敏感信息)

    def chat(self, messages, temperature=0.7, max_tokens=512, **kwargs):
        if not self.providers:
            raise LLMProviderError(self.name, "该链路没有可用后端", False)
        last_err = None
        self.last_trace = []
        for prov in self.providers:
            breaker = self.breakers[prov.name]
            try:
                breaker.before_call()
                out = prov.chat(messages, temperature, max_tokens, **kwargs)
                breaker.on_success()
                self.last_trace.append({"provider": prov.name, "ok": True})
                self.last_provider = prov.name
                self.last_usage = getattr(prov, "last_usage", {})
                return out
            except LLMProviderError as e:
                # 无论可不可重试都尝试下一个(不同后端钥匙/环境相互独立)
                breaker.on_failure()
                self.last_trace.append(
                    {"provider": prov.name, "ok": False, "reason": e.reason})
                last_err = e
        raise LLMProviderError(
            self.name,
            f"全部后端失败: {[t['provider'] for t in self.last_trace]}",
            True) from last_err


def load_profiles(config_path):
    """读并校验配置。非法/缺字段/字面钥匙一律显式报错。"""
    p = Path(config_path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在: {p}")
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"配置不是合法 JSON: {e}") from None
    if cfg.get("version") != 1:
        raise ConfigError("只支持 version=1 的后端配置")
    for key in ("profiles", "providers"):
        if not isinstance(cfg.get(key), dict):
            raise ConfigError(f"配置缺少对象字段: {key}")
    for pname, prov in cfg["providers"].items():
        # 安全红线: 配置里绝不允许写死钥匙, 只能写"去哪个环境变量取"
        if "api_key" in prov and str(prov["api_key"]).upper() != "EMPTY":
            raise ConfigError(
                f"后端 {pname} 把钥匙写进了配置文件, 请改用 api_key_env")
        for need in ("type", "base_url", "api_key_env", "model"):
            if need not in prov:
                raise ConfigError(f"后端 {pname} 缺少字段 {need}")
    return cfg


class LLMRouter:
    """按 profile(陪伴/机械守河)选链路, 对 daemon 暴露唯一 chat 入口。"""

    def __init__(self, config_path, config_dir=None):
        self.cfg = load_profiles(config_path)
        self.config_dir = Path(config_dir or
                              Path(config_path).parent).resolve()
        safety = self.cfg.get("safety", {})
        self.threshold = int(safety.get("circuit_breaker_threshold", 3))
        self.cooldown = int(safety.get("circuit_breaker_cooldown_sec", 60))
        self._providers = {}
        self._build_providers()
        self.chains = {}
        self.system_prompts = {}
        self._build_profiles()

    def _build_providers(self):
        for name, spec in self.cfg["providers"].items():
            if not spec.get("enabled", True):
                continue  # 未启用(如还没装 Ollama)就不构建
            self._providers[name] = OpenAICompatibleProvider(
                name=name,
                base_url=spec["base_url"],
                api_key_env=spec["api_key_env"],
                model=spec["model"],
                timeout=int(spec.get("timeout", 60)),
                extra_body=spec.get("extra_body"),
                secret_file=spec.get("secret_file"))

    def _build_profiles(self):
        for name, prof in self.cfg["profiles"].items():
            chain_names = [c for c in prof.get("chain", [])
                           if c in self._providers]
            if not chain_names:
                raise ConfigError(
                    f"profile {name} 的链路在已启用后端里为空")
            provs = [self._providers[c] for c in chain_names]
            self.chains[name] = FallbackChain(
                name, provs, threshold=self.threshold,
                cooldown_sec=self.cooldown)
            spf = prof.get("system_prompt_file")
            if spf:
                path = (self.config_dir / spf).resolve()
                if not path.exists():
                    # 配置既然指定了指令文件, 缺失就必须显式失败
                    raise ConfigError(
                        f"profile {name} 的指令文件不存在: {path}")
                self.system_prompts[name] = path.read_text(encoding="utf-8")

    def chat(self, profile, messages, system_extra="", **kw):
        if profile not in self.chains:
            raise ConfigError(f"未知 profile: {profile}")
        prof_cfg = self.cfg["profiles"][profile]
        conv = []
        base_sys = self.system_prompts.get(profile, "")
        system_text = "\n".join(x for x in (base_sys, system_extra) if x)
        if system_text:
            conv.append({"role": "system", "content": system_text})
        conv.extend(messages)
        chain = self.chains[profile]
        out = chain.chat(
            conv,
            temperature=kw.get("temperature", prof_cfg.get("temperature", 0.7)),
            max_tokens=kw.get("max_tokens", prof_cfg.get("max_tokens", 512)))
        # 供 daemon 记账: 谁答的、有没有发生切换、用量
        self.last_report = {
            "profile": profile,
            "provider": getattr(chain, "last_provider", None),
            "trace": chain.last_trace,
            "usage": getattr(chain, "last_usage", {}),
            "fell_back": len(chain.last_trace) > 1,
        }
        return out

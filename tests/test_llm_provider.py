"""llm_provider 离线单测: 全部 monkeypatch 网络出口, 不打真请求、不碰线上。

覆盖: 正常解析、故障切换、熔断、全失败、4xx 也切、配置红线(拒绝字面钥匙/
缺字段/版本错)、未启用后端被跳过、EMPTY 占位钥匙、未知 profile 报错。
"""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "federation"))

import llm_provider as lp  # noqa: E402


def _ok(text, usage=None):
    """打桩: 一个成功的 OpenAI 兼容响应。"""
    def _f(url, headers, body, timeout):
        return 200, json.dumps({
            "choices": [{"message": {"content": text}}],
            "usage": usage or {"prompt_tokens": 1, "completion_tokens": 1},
        })
    return _f


def _http_err(code):
    """打桩: 抛出指定状态码的 HTTPError。"""
    def _f(url, headers, body, timeout):
        raise urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b""))
    return _f


def _provider(name):
    # 测试统一用 EMPTY 占位钥匙, 不依赖任何真实环境变量
    return lp.OpenAICompatibleProvider(
        name, f"http://127.0.0.1/{name}", "EMPTY", f"model-{name}")


class TestProvider(unittest.TestCase):

    def setUp(self):
        self._orig = lp._http_post

    def tearDown(self):
        lp._http_post = self._orig

    def test_normal_parse_and_report(self):
        lp._http_post = _ok("你好阿阮", {"completion_tokens": 3})
        p = _provider("doubao")
        out = p.chat([{"role": "user", "content": "在吗"}], max_tokens=10)
        self.assertEqual(out, "你好阿阮")
        self.assertEqual(p.last_usage["completion_tokens"], 3)

    def test_5xx_marks_retryable(self):
        lp._http_post = _http_err(503)
        with self.assertRaises(lp.LLMProviderError) as cm:
            _provider("doubao").chat([])
        self.assertTrue(cm.exception.retryable)

    def test_4xx_nonretryable(self):
        lp._http_post = _http_err(400)
        with self.assertRaises(lp.LLMProviderError) as cm:
            _provider("doubao").chat([])
        self.assertFalse(cm.exception.retryable)

    def test_empty_key_placeholder(self):
        self.assertEqual(_provider("local")._key(), "EMPTY")

    def test_missing_key_raises(self):
        p = lp.OpenAICompatibleProvider(
            "x", "http://x", "NO_SUCH_ENV_VAR_ZZZ", "m")
        with self.assertRaises(lp.LLMProviderError):
            p._key()


class TestFallback(unittest.TestCase):

    def setUp(self):
        self._orig = lp._http_post

    def tearDown(self):
        lp._http_post = self._orig

    def test_failover_to_second(self):
        # 第一个后端 503, 第二个成功 —— 应切到第二个并标记 fell_back
        first, second = _provider("doubao"), _provider("local")

        def routed(url, headers, body, timeout):
            if "doubao" in url:
                raise urllib.error.HTTPError(url, 503, "e", {},
                                             io.BytesIO(b""))
            return 200, json.dumps(
                {"choices": [{"message": {"content": "本地顶上"}}],
                 "usage": {}})
        lp._http_post = routed
        chain = lp.FallbackChain("c", [first, second])
        out = chain.chat([])
        self.assertEqual(out, "本地顶上")
        self.assertFalse(chain.last_trace[0]["ok"])
        self.assertTrue(chain.last_trace[1]["ok"])

    def test_401_still_falls_back(self):
        # 后端相互独立: 第一个 401(不可重试) 也应尝试第二个
        first, second = _provider("doubao"), _provider("local")

        def routed(url, headers, body, timeout):
            if "doubao" in url:
                raise urllib.error.HTTPError(url, 401, "e", {},
                                             io.BytesIO(b""))
            return _ok("换后端成功")(url, headers, body, timeout)
        lp._http_post = routed
        out = lp.FallbackChain("c", [first, second]).chat([])
        self.assertEqual(out, "换后端成功")

    def test_all_fail_raises(self):
        lp._http_post = _http_err(500)
        chain = lp.FallbackChain("c", [_provider("a"), _provider("b")])
        with self.assertRaises(lp.LLMProviderError):
            chain.chat([])
        self.assertEqual(len(chain.last_trace), 2)

    def test_empty_chain_raises(self):
        with self.assertRaises(lp.LLMProviderError):
            lp.FallbackChain("c", []).chat([])


class TestBreaker(unittest.TestCase):

    def test_opens_after_threshold(self):
        cb = lp.CircuitBreaker("x", threshold=3, cooldown_sec=60)
        for _ in range(3):
            cb.on_failure()
        with self.assertRaises(lp.LLMProviderError):
            cb.before_call()  # 熔断中: 不再真正调用

    def test_success_resets(self):
        cb = lp.CircuitBreaker("x", threshold=3, cooldown_sec=60)
        cb.on_failure()
        cb.on_failure()
        cb.on_success()
        self.assertEqual(cb.fail_count, 0)
        cb.before_call()  # 不抛


def _write_cfg(obj):
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(obj, f)
    f.close()
    return f.name


class TestConfig(unittest.TestCase):

    def test_reject_literal_key(self):
        path = _write_cfg({
            "version": 1,
            "profiles": {},
            "providers": {"a": {
                "type": "openai_compatible", "base_url": "x",
                "api_key": "sk-real-secret", "model": "m"}}})
        with self.assertRaises(lp.ConfigError):
            lp.load_profiles(path)

    def test_missing_field(self):
        path = _write_cfg({
            "version": 1, "profiles": {},
            "providers": {"a": {"type": "t", "base_url": "x"}}})
        with self.assertRaises(lp.ConfigError):
            lp.load_profiles(path)

    def test_bad_version(self):
        path = _write_cfg({"version": 9, "profiles": {}, "providers": {}})
        with self.assertRaises(lp.ConfigError):
            lp.load_profiles(path)

    def test_real_config_disabled_provider_skipped(self):
        # 用仓库里真实配置建路由: local 未启用应被跳过, 链路只剩 doubao
        router = lp.LLMRouter(ROOT / "config" / "llm_profiles.json")
        comp = [p.name for p in router.chains["companion"].providers]
        mech = [p.name for p in router.chains["mechanical"].providers]
        self.assertEqual(comp, ["doubao"])
        self.assertEqual(mech, ["doubao"])
        self.assertNotIn("local-emergency", router._providers)

    def test_unknown_profile(self):
        router = lp.LLMRouter(ROOT / "config" / "llm_profiles.json")
        with self.assertRaises(lp.ConfigError):
            router.chat("not-exist", [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

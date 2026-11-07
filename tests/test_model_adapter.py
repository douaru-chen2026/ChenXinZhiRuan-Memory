# -*- coding: utf-8 -*-
"""模型适配层单元测试：离线确定性能力、切批、限制感知，不依赖网络与密钥。"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chenxin import config
from chenxin.model_adapter import (
    AuthError,
    ModelClient,
    chunk_by_budget,
    cosine,
    estimate_tokens,
    offline_vector,
)


class TestOfflineVector(unittest.TestCase):
    """离线向量的确定性与区分度。"""

    def test_deterministic(self):
        self.assertEqual(
            offline_vector("一句固定的话"),
            offline_vector("一句固定的话"),
        )

    def test_similar_closer_than_unrelated(self):
        anchor = offline_vector("他对我占有欲很强，还说暧昧的话")
        close = offline_vector("他占有欲很强，总说暧昧的话")
        unrelated = offline_vector("今天天气真好，我们一起去吃饭")
        self.assertGreater(cosine(anchor, close), cosine(anchor, unrelated))

    def test_norm_unit(self):
        vec = offline_vector("归一化校验")
        norm = sum(x * x for x in vec) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)


class TestChunking(unittest.TestCase):
    """按字符预算切批。"""

    def test_respects_budget(self):
        items = ["x" * 100] * 10
        batches = chunk_by_budget(items, char_budget=250)
        self.assertTrue(all(
            sum(len(x) for x in batch) <= 250 for batch in batches))
        self.assertEqual(sum(len(b) for b in batches), 10)

    def test_oversized_single_item(self):
        items = ["y" * 500, "z"]
        batches = chunk_by_budget(items, char_budget=100)
        self.assertEqual(batches[0], [items[0]])


class TestDegradation(unittest.TestCase):
    """无密钥时自动降级。"""

    def setUp(self):
        # 强制使用空配置，避免宿主环境变量干扰。
        self.models = {role: config.ModelConfig(role=role)
                       for role in config.ALL_ROLES}
        self.client = ModelClient(self.models)

    def test_not_live(self):
        for role in config.ALL_ROLES:
            self.assertFalse(self.client.is_live(role))

    def test_chat_returns_none(self):
        self.assertIsNone(
            self.client.chat("router", [{"role": "user", "content": "hi"}]))

    def test_embed_returns_none(self):
        self.assertIsNone(self.client.embed(["hi"]))


class TestPlaceholder(unittest.TestCase):
    """占位符未展开时不算可用。"""

    def test_placeholder_not_usable(self):
        cfg = config.ModelConfig(
            role="router", base_url="https://x", model="m",
            api_key="${SOME_MISSING_KEY}")
        self.assertFalse(cfg.usable)

    def test_real_key_usable(self):
        cfg = config.ModelConfig(
            role="router", base_url="https://x", model="m", api_key="sk-real")
        self.assertTrue(cfg.usable)


class TestEstimate(unittest.TestCase):

    def test_tokens_positive(self):
        self.assertGreater(estimate_tokens("中文内容"), 0)


if __name__ == "__main__":
    unittest.main()

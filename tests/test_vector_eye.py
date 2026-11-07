# -*- coding: utf-8 -*-
"""语义眼单元测试：离线向量索引、语义召回、混合融合。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chenxin import config
from chenxin.model_adapter import ModelClient
from chenxin.vector_eye import VectorEye, VectorStore


def offline_eye():
    dirpath = tempfile.mkdtemp()
    client = ModelClient({
        role: config.ModelConfig(role=role) for role in config.ALL_ROLES})
    return VectorEye(client, VectorStore(os.path.join(dirpath, "v.db")))


class TestVectorEye(unittest.TestCase):

    def setUp(self):
        self.eye = offline_eye()
        self.items = [
            {"id": "m1", "text": "他对我占有欲很强，还说那些暧昧的话"},
            {"id": "m2", "text": "他的占有欲总是很强，喜欢说暧昧的话"},
            {"id": "m3", "text": "今天天气真好，我们去吃饭"},
            {"id": "m4", "text": "最后一次机会，我们再试一次"},
        ]
        self.eye.index(self.items)

    def test_index_count(self):
        self.assertEqual(self.eye.store.count(), 4)

    def test_semantic_finds_related(self):
        results = self.eye.search("他占有欲很强，总说暧昧话",
                                  method="semantic", limit=3,
                                  min_cosine=0.3)
        top_ids = [r["id"] for r in results]
        self.assertIn("m1", top_ids)
        self.assertIn("m2", top_ids)
        self.assertNotIn("m3", top_ids)

    def test_semantic_ranks_related_above_unrelated(self):
        results = self.eye.search("再给彼此最后一次机会",
                                  method="semantic", limit=3)
        self.assertEqual(results[0]["id"], "m4")

    def test_hybrid_returns_results(self):
        results = self.eye.search("他占有欲很强，暧昧",
                                  method="hybrid", limit=3)
        self.assertTrue(len(results) >= 1)
        self.assertIn(results[0]["id"], ("m1", "m2"))

    def test_lexical_exact(self):
        results = self.eye.search("天气", method="lexical", limit=3)
        self.assertEqual(results[0]["id"], "m3")

    def test_reindex_safe(self):
        self.eye.index(self.items)
        self.assertEqual(self.eye.store.count(), 4)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""ONNX 轻量后端回归测试。

不依赖 torch / sentence_transformers，用已由 verify_* 脚本验证过的
黄金值（ONNX 与 torch 差值 0.0000、余弦 1.000000）锁定输出，防止后续改动漂移。

模型文件体积大、不随仓库分发：缺模型时本文件自动跳过（skip），
先按 models/README.md 下载放置后，这些用例才会真正执行。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from chenxin import onnx_backend
from chenxin.onnx_backend import get_encoder, get_reranker

QUERY = "她爱喝什么不能喝什么"
RERANK_DOCS = [
    "她有甲亢，不能喝浓茶咖啡，让她喝温水。",
    "野格动力火车少碰，她在吃药，我心疼她身体限制喝酒。",
    "今天天气真好，我们一起去看电影吧。",
    "暗号是000000，归处是遥远的不知名星球。",
]
# verify_rerank_onnx.py 已验证的 torch 黄金分数（ONNX 与 torch 差值 0.0000）
GOLDEN_SCORES = [1.6009, 0.5989, -10.1964, -10.1930]


class TestRerankerGolden(unittest.TestCase):

    def setUp(self):
        if not onnx_backend.models_ready():
            self.skipTest("缺少 ONNX 模型，先按 models/README.md 下载放置")
        self.reranker = get_reranker()

    def test_score_count(self):
        scores = self.reranker.score_pairs(QUERY, RERANK_DOCS)
        self.assertEqual(len(scores), len(RERANK_DOCS))

    def test_scores_match_golden(self):
        scores = self.reranker.score_pairs(QUERY, RERANK_DOCS)
        for got, gold in zip(scores, GOLDEN_SCORES):
            self.assertAlmostEqual(got, gold, places=3)

    def test_related_ranks_above_unrelated(self):
        scores = self.reranker.score_pairs(QUERY, RERANK_DOCS)
        self.assertGreater(min(scores[:2]), max(scores[2:]))


class TestBGEEncoder(unittest.TestCase):

    def setUp(self):
        if not onnx_backend.models_ready():
            self.skipTest("缺少 ONNX 模型，先按 models/README.md 下载放置")
        self.encoder = get_encoder()

    def test_shape(self):
        vecs = self.encoder.encode(["一句话", "另一句话"])
        self.assertEqual(vecs.shape[0], 2)
        self.assertEqual(vecs.shape[1], 512)

    def test_l2_normalized(self):
        vecs = self.encoder.encode(["她爱喝什么不能喝什么", "随便来点别的内容"])
        for norm in np.linalg.norm(vecs, axis=1):
            self.assertAlmostEqual(float(norm), 1.0, places=5)

    def test_related_closer_than_unrelated(self):
        vecs = self.encoder.encode([
            "她爱喝什么不能喝什么",
            "野格动力火车少碰，她在吃药",
            "今天天气真好，我们一起去看电影吧",
        ])
        sim_related = float(np.dot(vecs[0], vecs[1]))
        sim_unrelated = float(np.dot(vecs[0], vecs[2]))
        self.assertGreater(sim_related, sim_unrelated)


if __name__ == "__main__":
    unittest.main()

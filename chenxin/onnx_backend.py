# -*- coding: utf-8 -*-
"""轻量 ONNX 推理后端：只依赖 onnxruntime + tokenizers + numpy。

运行时不再 import torch / transformers / sentence_transformers，
把“开口前召回”的冷启动从约 90 秒（重框架导入）降到秒级。

提供两个进程内单例：
    get_encoder()   BGE 句向量编码器（CLS 池化 + L2 归一化）
    get_reranker()  Cross-Encoder 相关性打分器
"""
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from . import config

MODELS_DIR = config.ROOT / "models"
_BGE_ONNX = MODELS_DIR / "bge-small-zh.onnx"
_BGE_TOK = MODELS_DIR / "bge-tokenizer.json"
_RER_ONNX = MODELS_DIR / "reranker.onnx"
_RER_TOK = MODELS_DIR / "reranker-tokenizer.json"

_encoder = None
_reranker = None


def _pad(encodings, pad_id):
    """把一组等字段的 encoding 补齐到批次内最长，返回三个 int64 数组。"""
    max_len = max(len(e.ids) for e in encodings)
    ids, mask = [], []
    for enc in encodings:
        cur = len(enc.ids)
        pad_n = max_len - cur
        ids.append(list(enc.ids) + [pad_id] * pad_n)
        mask.append(list(enc.attention_mask) + [0] * pad_n)
    return (
        np.array(ids, dtype=np.int64),
        np.array(mask, dtype=np.int64),
    )


class BGEEncoder:
    """bge-small-zh：BERT 结构，取 CLS（index 0）作为句向量。"""

    PAD_ID = 0

    def __init__(self, threads=4):
        self.tokenizer = Tokenizer.from_file(str(_BGE_TOK))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(threads, _cpu()))
        self.session = ort.InferenceSession(
            str(_BGE_ONNX), sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

    def encode(self, texts, max_length=512):
        """编码一批文本，返回 L2 归一化后的 numpy 数组 [n, 512]。"""
        if isinstance(texts, str):
            texts = [texts]
        encs = [self.tokenizer.encode(t[:max_length * 4]) for t in texts]
        ids, mask = _pad(encs, self.PAD_ID)
        token_type = np.zeros_like(ids)
        last = self.session.run(
            ["last_hidden_state"],
            {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": token_type,
            },
        )[0]
        cls = last[:, 0, :]
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        return cls / np.maximum(norms, 1e-12)


class Reranker:
    """bge-reranker-base：对 (query, doc) 对输出相关性 logit。"""

    PAD_ID = 1

    def __init__(self, threads=4):
        self.tokenizer = Tokenizer.from_file(str(_RER_TOK))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(threads, _cpu()))
        self.session = ort.InferenceSession(
            str(_RER_ONNX), sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

    def score_pairs(self, query, docs, max_length=256, batch_size=16):
        """对 (query, doc) 对分批打分，返回与 docs 等长的 float 列表。"""
        scores = []
        for i in range(0, len(docs), batch_size):
            batch = docs[i:i + batch_size]
            encs = [
                self.tokenizer.encode(query, doc[:max_length * 4])
                for doc in batch
            ]
            for enc in encs:
                if len(enc.ids) > max_length:
                    enc.truncate(max_length)
            ids, mask = _pad(encs, self.PAD_ID)
            logits = self.session.run(
                ["logits"],
                {"input_ids": ids, "attention_mask": mask},
            )[0]
            scores.extend(logits.reshape(-1).tolist())
        return scores


def _cpu():
    import os
    return os.cpu_count() or 1


def get_encoder():
    """BGE 编码器进程内单例。"""
    global _encoder
    if _encoder is None:
        _encoder = BGEEncoder()
    return _encoder


def get_reranker():
    """Cross-Encoder 打分器进程内单例。"""
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker


MODEL_PATHS = (_BGE_ONNX, _BGE_TOK, _RER_ONNX, _RER_TOK)


def models_ready():
    """四个 ONNX/词表文件是否都已就位。

    模型体积大、不随仓库分发，需按 models/README.md 自行下载放置；
    测试据此在缺模型时跳过而不是报错。
    """
    return all(path.exists() for path in MODEL_PATHS)

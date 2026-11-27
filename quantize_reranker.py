# -*- coding: utf-8 -*-
"""一次性：把 fp32 reranker 动态量化为 int8，缩小体积、加快 CPU 加载与推理。"""
from pathlib import Path

from onnxruntime.quantization import QuantType, quantize_dynamic

MODELS = Path(__file__).resolve().parent / "models"
src = MODELS / "reranker.onnx"
dst = MODELS / "reranker-int8.onnx"

quantize_dynamic(
    model_input=str(src),
    model_output=str(dst),
    weight_type=QuantType.QInt8,
)

print("量化完成:", dst, "大小 %.1f MB" % (dst.stat().st_size / 1024 / 1024))

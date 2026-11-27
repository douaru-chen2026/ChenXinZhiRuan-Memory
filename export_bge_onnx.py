# -*- coding: utf-8 -*-
"""一次性导出：把 bge-small-zh（BERT, CLS 池化）导出为动态轴 ONNX。

运行时只取 last_hidden_state 的 CLS（index 0）再 L2 归一化，
不再依赖 torch / sentence_transformers。
"""
from pathlib import Path

import torch
from transformers import AutoModel

OUT = Path(__file__).resolve().parent / "models"
OUT.mkdir(parents=True, exist_ok=True)
target = OUT / "bge-small-zh.onnx"


class BGEWrapper(torch.nn.Module):
    """只暴露三个输入，返回 last_hidden_state，规避 ONNX trace 参数冲突。"""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, input_ids, attention_mask, token_type_ids):
        last_hidden, _pooler = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            return_dict=False,
            use_cache=False,
        )
        return last_hidden


base = AutoModel.from_pretrained("BAAI/bge-small-zh-v1.5")
base.eval()
model = BGEWrapper(base)
model.eval()

dummy_ids = torch.ones((2, 8), dtype=torch.long)
dummy_mask = torch.ones((2, 8), dtype=torch.long)
dummy_type = torch.zeros((2, 8), dtype=torch.long)

with torch.no_grad():
    torch.onnx.export(
        model,
        (dummy_ids, dummy_mask, dummy_type),
        str(target),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "seq"},
            "attention_mask": {0: "batch", 1: "seq"},
            "token_type_ids": {0: "batch", 1: "seq"},
            "last_hidden_state": {0: "batch", 1: "seq"},
        },
        opset_version=17,
        dynamo=False,
    )

print("已导出:", target, "大小 %.1f MB" % (target.stat().st_size / 1024 / 1024))

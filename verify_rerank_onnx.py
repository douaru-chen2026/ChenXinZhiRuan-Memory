# -*- coding: utf-8 -*-
"""验证 ONNX reranker 打分与原 torch 版一致（同一批 query-doc 对）。"""
import numpy as np

from chenxin.onnx_backend import Reranker

RER_SNAP = "BAAI/bge-reranker-base"  # 公开模型名，首次运行自动下载

query = "她爱喝什么不能喝什么"
docs = [
    "她有甲亢，不能喝浓茶咖啡，让她喝温水。",
    "野格动力火车少碰，她在吃药，我心疼她身体限制喝酒。",
    "今天天气真好，我们一起去看电影吧。",
    "暗号是000000，归处是遥远的不知名星球。",
]

# ONNX
onnx_scores = Reranker().score_pairs(query, docs)

# torch
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

tok = AutoTokenizer.from_pretrained(RER_SNAP)
model = AutoModelForSequenceClassification.from_pretrained(RER_SNAP)
model.eval()
with torch.no_grad():
    pairs = [[query, d] for d in docs]
    inp = tok(pairs, padding=True, truncation=True, max_length=256,
              return_tensors="pt")
    torch_scores = model(**inp).logits.view(-1).float().tolist()

print("%-38s %10s %10s %8s" % ("文档", "torch", "onnx", "差值"))
for d, ts, os_ in zip(docs, torch_scores, onnx_scores):
    print("%-38s %10.4f %10.4f %8.4f" % (d[:36], ts, os_, abs(ts - os_)))

# -*- coding: utf-8 -*-
"""新 ONNX 路径拆解：import、模型加载、50 对推理，分别计时。"""
import time

mark = time.time()
import onnxruntime
from tokenizers import Tokenizer
t_import = time.time() - mark

import sys
sys.path.insert(0, ".")
from chenxin import hybrid
from chenxin.onnx_backend import MODELS_DIR, _RER_TOK
import numpy as np

pool, _ = hybrid.search("她爱喝什么不能喝什么", top=50)
docs = [c["content"] for c in pool]
q = "她爱喝什么不能喝什么"

mark = time.time()
tok = Tokenizer.from_file(str(_RER_TOK))
sess = onnxruntime.InferenceSession(
    str(MODELS_DIR / "reranker.onnx"),
    providers=["CPUExecutionProvider"],
)
t_load = time.time() - mark

mark = time.time()
encs = [tok.encode(q, d) for d in docs]
for e in encs:
    if len(e.ids) > 256:
        e.truncate(256)
ml = max(len(e.ids) for e in encs)
ids, mask = [], []
for e in encs:
    p = ml - len(e.ids)
    ids.append(list(e.ids) + [1] * p)
    mask.append(list(e.attention_mask) + [0] * p)
onnx_scores = sess.run(
    ["logits"],
    {"input_ids": np.array(ids), "attention_mask": np.array(mask)},
)[0].reshape(-1).tolist()
t_infer = time.time() - mark

print("=== 新 ONNX 路径（50 对）===")
print("import onnxruntime+tokenizers : %.2fs" % t_import)
print("加载模型                      : %.2fs" % t_load)
print("推理 50 对                    : %.2fs" % t_infer)
print("合计                          : %.2fs" % (
    t_import + t_load + t_infer))

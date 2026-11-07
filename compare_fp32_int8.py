# -*- coding: utf-8 -*-
"""同一 50 条候选上，fp32 vs int8 精排 top10 并排对比，明确差异。"""
import sys
sys.path.insert(0, ".")

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from chenxin import hybrid
from chenxin.onnx_backend import MODELS_DIR, _RER_TOK

q = "她爱喝什么不能喝什么"
pool, _ = hybrid.search(q, top=50)
docs = [c["content"] for c in pool]

tok = Tokenizer.from_file(str(_RER_TOK))


def score(onnx_name):
    sess = ort.InferenceSession(
        str(MODELS_DIR / onnx_name),
        providers=["CPUExecutionProvider"],
    )
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
    return sess.run(
        ["logits"],
        {"input_ids": np.array(ids), "attention_mask": np.array(mask)},
    )[0].reshape(-1).tolist()


fp32 = score("reranker.onnx")
int8 = score("reranker-int8.onnx")

fp_rank = sorted(range(50), key=lambda i: -fp32[i])
i8_rank = sorted(range(50), key=lambda i: -int8[i])

print("%-4s %-26s %-26s" % ("名次", "fp32(候选号:分)", "int8(候选号:分)"))
for k in range(10):
    a, b = fp_rank[k], i8_rank[k]
    mark = "" if a == b else "  <-- 不同"
    print("%-4d #%-3d %-8.3f            #%-3d %-8.3f%s" % (
        k + 1, a, fp32[a], b, int8[b], mark))

fp_set, i8_set = set(fp_rank[:10]), set(i8_rank[:10])
print("\ntop10 重合: %d/10" % len(fp_set & i8_set))
print("fp32 有、int8 漏掉的候选:", sorted(fp_set - i8_set))
print("int8 新拉进 top10 的候选:", sorted(i8_set - fp_set))
for i in sorted(fp_set - i8_set):
    print("  被漏掉的 #%d: %s" % (i, docs[i][:30]))
for i in sorted(i8_set - fp_set):
    print("  被拉进的 #%d: %s" % (i, docs[i][:30]))

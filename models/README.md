# models/ 模型文件说明

本目录的模型文件体积较大（合计约 1.5GB），不纳入 Git，需按下面清单自行放置。

| 文件 | 来源 | 说明 |
|---|---|---|
| `bge-small-zh.onnx` | 运行 `export_bge_onnx.py` | 从 BAAI/bge-small-zh-v1.5 导出，CLS 池化、L2 归一化 |
| `bge-tokenizer.json` | bge-small-zh 快照内 `tokenizer.json` | 句向量分词器 |
| `reranker.onnx` | BAAI/bge-reranker-base 官方自带 `onnx/model.onnx` | fp32 精排，默认使用 |
| `reranker-tokenizer.json` | reranker 快照内 `tokenizer.json` | 精排分词器 |
| `reranker-int8.onnx` | 运行 `quantize_reranker.py` | 动态量化备用版，默认不启用 |

运行时只依赖 `onnxruntime` + `tokenizers` + `numpy`，无需安装 PyTorch / transformers。

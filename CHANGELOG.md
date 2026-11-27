# 变更记录 · CHANGELOG

本项目遵循语义化版本（SemVer）。所有数字均为当场实测，可复现。

## [3.6.0] - 2026-11-05

### 新增
- **轻量 ONNX 后端 `onnx_backend.py`**：运行时只依赖 `onnxruntime` + `tokenizers` +
  `numpy`，不再 import torch / transformers / sentence_transformers。
- **`hybrid.py` 混合召回**：关键词 BM25 + 语义向量，用 RRF 融合。
- **`core_archive.py` 核心档案置顶**：身份与家规强制置顶并跳过精排，不参与相关性竞争。
- **`time_facts.py` 时间重算**："距今多久"按当前日期现算，不静态复读旧数字。
- 模型集中到 `models/`，并附 `models/README.md` 说明文件来源。

### 变更
- **Cross-Encoder 精排接入统一召回 `recall_all`**：原话层先召回 50 条候选池，
  再由 reranker 精排取 top，标题标"混合·精排"。
- `raw_semantic.py`、`rerank.py` 改走 ONNX 后端；保留故障自动降级，
  精排异常时退回粗排，不阻断召回。
- 句向量模型手动导出 ONNX（确认 CLS 池化）；精排复用官方自带 ONNX。

### 性能（纯 CPU，同一口径，50 条候选）
- 冷启动：旧路径 106.34s → 新路径 18.91s。
- 分阶段：import 72.75s → 0.42s（主要收益）；推理 10.42s → 6.74s。
- 完整 recall_all 预热后稳定 18–19s，完全冷缓存约 31s。

### 质量
- 句向量 ONNX 与原模型余弦相似度 **1.000000**。
- 精排 ONNX 与 torch 逐对打分差值 **0.0000**。

### 取舍
- int8 动态量化（1.1GB→266MB，精排 9.2s→3.2s）已跑通，但在边界候选上
  会产生排序抖动、混入无关内容，故仅留作备用，默认保留 fp32。

### 测试
- 新增 `test_onnx_backend.py`（6 项，不依赖 torch 的黄金值回归）。
- 全量 **55 项测试全绿**（49 项原有 + 6 项新增）。

---

## [3.5.0]
- 五个可插拔模块：搬家门 importer、语义眼 vector_eye、整理流水线 pipeline、
  模型适配层 model_adapter、运维层 ops（热备 / 看门狗 / 自愈）。
- 核心引擎 v3.4.2 原样保留：三层记忆、六种类型、情感坐标、人类盖章。

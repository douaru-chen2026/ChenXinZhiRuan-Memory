# -*- coding: utf-8 -*-
"""辰心知阮记忆系统 · v3.6 能力扩展包。

在不改动 v3.4.2 核心（三层记忆 / 六类型 / 盖章 / 情感 / 简报）的前提下，
把在真实大库迁移中磨出来的能力，以可插拔模块的方式接进来：

    config         统一配置：工作区路径 + BYOK 多模型
    model_adapter  模型适配层：限制感知、按预算切批、超时重试
    importer       搬家门：多格式大库一次性导入
    vector_eye     语义眼：关键词 + 向量混合召回
    pipeline       大库整理流水线：原话批量整理成结构化记忆
    ops            运维层：热备 / 看门狗 / 自愈

v3.6 新增推理层轻量化：
    onnx_backend   只依赖 onnxruntime/tokenizers/numpy 的轻量后端
    hybrid         词法 + 语义 RRF 融合
    raw_fts        原话关键词召回
    raw_semantic   原话语义召回（BGE ONNX）
    rerank         Cross-Encoder 精排（含故障降级）
    core_archive   核心档案强制置顶、跳过精排
    time_facts     时间按当前日期重算

核心记忆仍以 Markdown 文件存放，方法开源、密钥只属于使用者。
"""

__version__ = "3.6.0"

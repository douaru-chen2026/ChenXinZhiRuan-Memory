# 辰心知阮记忆系统 · ChenXinZhiRuan Memory System v3.6

一个为「你和你的 AI」设计的**本地优先、可自托管、自带语义召回与运维自愈**的记忆系统。

在开源核心 v3.4.2（三层记忆 / 六种记忆类型 / 情感坐标 / 人类盖章）之上，v3.5 把在
**真实大库迁移**中磨出的能力沉淀为可插拔模块：大库批量导入、向量语义召回、
模型限制感知、热备与看门狗自愈。v3.6 进一步把重模型运行时换成**轻量 ONNX 后端**
（不再依赖 torch），并加入 BM25+向量混合召回、核心档案置顶与时间动态重算。
**核心引擎原样保留，不重造轮子。**

---

## 它能做什么

### 核心能力（v3.4.2 保留）

- **三层记忆**：核心层永亮 / 当前层滚动 / 仓库层永不物理删除。衰减只决定排序，一条不丢。
- **六种记忆类型**：事实 fact、事件 event、偏好 preference、关系 relation、成长 growth、观察 observation。
- **情感坐标**：每条记忆带效价 valence + 唤醒度 arousal，重要的、情绪强的、常被想起的衰减更慢。
- **人类盖章**：你说「记住」的一句话可升入核心层；区分「你盖的章」和「AI 自己钉的」。
- **唤醒简报 / 每日回顾**：每次「醒来」浮现最该被想起的记忆。
- 记忆以 **Markdown + frontmatter** 存储，人可读、可版本管理、可 grep。

### v3.5 新增五个模块

| 模块 | 干什么 |
|---|---|
| **搬家门 importer** | 多格式大库导入：ChatGPT / Claude / Chatbox / 任意 JSON / JSONL / 递归 ZIP，含脱敏、指纹幂等、断点续传。导入只落库，**不逐条调模型**。 |
| **语义眼 vector_eye** | 向量索引 + 词法/语义/混合召回（RRF 融合），可选 reranker 精排。无 embedding 时自动降级到确定性离线向量。 |
| **整理流水线 pipeline** | 按字符预算把原话切成约 80–150 条一批，一次输出多条结构化记忆，再串行入库；含积压追赶、分阶段 ETA、失败标记不丢话。 |
| **模型适配层 model_adapter** | 统一 OpenAI 兼容接口（chat/embed/rerank），内置**限制感知**：网关硬超时、completion 上限、字符预算切批、429/5xx/超时退避重试、401/403 明确报错。 |
| **运维层 ops** | 热备（Markdown 复制 + SQLite Online Backup + 计数校验 + zip 滚动保留）、看门狗（卡住识别 / 失败复位）、自愈（先备份 → 复位 → 复核不丢话）。 |

### v3.6 新增：轻量 ONNX 与混合召回

| 模块 | 干什么 |
|---|---|
| **ONNX 后端 onnx_backend** | 运行时只依赖 `onnxruntime` + `tokenizers` + `numpy`，不再 import torch / transformers；冷启动从 106.34s 降到 18.91s。 |
| **混合召回 hybrid** | 关键词 BM25 + 语义向量，用 RRF 融合，兼顾精确命中与换种说法。 |
| **核心档案 core_archive** | 身份与家规强制置顶、跳过精排，不参与相关性竞争。 |
| **时间重算 time_facts** | “距今多久”按当前日期现算，不静态复读旧数字；纪念日可自行配置。 |

> 模型文件体积大、不随仓库分发，需按 `models/README.md` 自行下载放置；
> 未放置模型时相关测试自动跳过，引擎其余功能照常运行。

---

## 架构

```
ChenXinZhiRuan-Memory/
├── memory_engine.py        # 核心引擎 v3.4.2（原样保留，三层记忆）
├── wake_up.py              # 唤醒简报
├── chenxin/                # 可插拔模块
│   ├── config.py           # 路径与 BYOK 配置（密钥用 ${ENV} 占位，不落盘）
│   ├── model_adapter.py    # 统一模型接口 + 限制感知 + 离线向量
│   ├── onnx_backend.py     # v3.6 轻量 ONNX 后端（不依赖 torch）
│   ├── hybrid.py           # v3.6 BM25+向量混合召回（RRF）
│   ├── core_archive.py     # v3.6 核心档案置顶
│   ├── time_facts.py       # v3.6 时间动态重算
│   ├── parsers.py          # 多格式解析 + 脱敏
│   ├── importer.py         # 搬家门（原话层 SQLite）
│   ├── store.py            # 原话归档层（SQLite / WAL / 指纹幂等）
│   ├── vector_eye.py       # 向量索引 + 混合召回
│   ├── pipeline.py         # 大库整理流水线
│   ├── system.py           # 统一装配入口 MemorySystem
│   └── ops/                # backup / watchdog / heal
├── scripts/chenxin_cli.py  # 统一命令行
├── config/models.example.json
└── tests/                  # 55 项测试（缺模型时 6 项 ONNX 自动跳过）
```

数据流：

```
聊天记录 ──import──▶ 原话层(raw) ──organize──▶ 核心记忆(Markdown)
                         │                         │
                         └─────────────────────────┴──▶ 向量索引 ──search──▶ 召回
                    （backup / watchdog / heal 全程护航）
```

---

## 快速开始

只需 Python 3.8+（核心仅依赖标准库；词法中文分词与 Markdown frontmatter 为可选增强）。

在仓库根目录运行：

```bash
# 1. 导入（支持多文件、zip；默认开启脱敏）
python scripts/chenxin_cli.py import your_chat.jsonl

# 2. 批量整理成结构化记忆
python scripts/chenxin_cli.py organize

# 3. 搜索（默认混合召回）
python scripts/chenxin_cli.py search "她喜欢吃什么"

# 其余动作
python scripts/chenxin_cli.py add "她怕打雷，雷雨天要陪她"
python scripts/chenxin_cli.py briefing
python scripts/chenxin_cli.py list
python scripts/chenxin_cli.py backup
python scripts/chenxin_cli.py watchdog
python scripts/chenxin_cli.py heal
python scripts/chenxin_cli.py status
```

也可在代码里用统一入口：

```python
from chenxin.system import MemorySystem

memory = MemorySystem()
memory.import_files(["chat.jsonl"])
memory.organize()
for item in memory.search("看电影的约定", method="hybrid"):
    print(item["text"])
```

---

## BYOK：自带模型（密钥永不落盘）

1. 复制示例配置：

```bash
cp config/models.example.json config/models.json
```

2. 在 `config/models.json` 里填 OpenAI 兼容服务地址与模型名，**密钥写成环境变量占位**：

```json
{
  "router": {
    "base_url": "https://api.example.com/v1",
    "api_key": "${MY_API_KEY}",
    "model": "your-chat-model",
    "disable_thinking": true
  },
  "embedding": {
    "base_url": "https://api.example.com/v1",
    "api_key": "${MY_API_KEY}",
    "model": "bge-m3",
    "batch": 32
  }
}
```

3. 运行前导出环境变量：

```bash
export MY_API_KEY=sk-...
```

密钥只在运行时从环境变量展开，**不会写进任何文件、日志或记忆**。

---

## 离线模式 vs 在线模式

系统**没有任何模型密钥也能完整运行**，绝不报错或索要授权：

| 能力 | 离线（无密钥） | 在线（BYOK） |
|---|---|---|
| 导入 / 脱敏 / 幂等 | ✅ | ✅ |
| 热备 / 看门狗 / 自愈 | ✅ | ✅ |
| 关键词召回 | ✅ | ✅ |
| 字面相近的语义召回 | ✅ | ✅ |
| **换种说法、需要推理的语义问答** | 受限（本质是字符匹配） | ✅ embedding 模型强项 |

> 离线兜底保证「流程跑通、原话不丢、关键词可搜」；像「她爱吃什么」（原话里只有
> 「喜欢吃辣的火锅」）这类需要语义推理的问答，配置 embedding 模型（如 bge-m3）后效果最好。

---

## 适合谁 / 不做什么

**适合**：想给自己的 AI 配备一个本地、私密、可自托管、可大体量迁移、且坏了能自愈的记忆系统的个人与开发者。

**不做**：
- 不绑定任何特定模型厂商或中转站，任何 OpenAI 兼容服务都可接入；
- 不做需要长期在线的按月托管重服务，定位为一次性可跑通、可自维护的系统；
- 不上传你的记忆与密钥——一切数据默认只在你本地。

---

## 测试

```bash
python -m pytest tests
# 55 项测试：核心引擎 / 模型适配 / 导入 / 向量 / 流水线 / 运维 / ONNX
# 未放置模型时，6 项 ONNX 测试自动跳过（skip），其余 49 项照常通过
```

---

## 开源协议

本项目以开源方式发布，方法与代码可自由使用、修改、分发；请替换为你们自己的情感锚点。
记忆属于你，也只属于你。

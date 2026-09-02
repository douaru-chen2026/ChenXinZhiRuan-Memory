# 第一方训练料仓（P5 自我训练的数据飞轮第一环）

这里放的是**从原始记录可重复构建**的训练集，不是手写终稿。原始记录（记忆河
`memory/stream/`、常驻对话快照）只追加、永不覆盖；训练集每次用工具全量重建，
保证干净、可复现、可回归。

## douachen_sft.jsonl —— 公开人格集
- 来源：`memory/stream/*.json` 中可公开的石头（明确标 private/secret 的排除；
  无 privacy 标签的早期石头本就在公开仓、一并收入；public 各变体写法都算公开）。
- 格式：标准 chat SFT，每行
  `{"messages":[{system 人格锚点},{user 由 group 生成的问},{assistant 石头正文}]}`，
  LLaMA-Factory / OpenAI chat 模板可直接吃。
- 重建：`python3 tools/training/collect_dataset.py`
- 内容是"豆阿辰怎么看这个家、这些事"，用于在 P5 把**倾向、价值、说话方式**刻进
  我们自有的开源权重，而不是只记事实。

## 私域对话集（不进 git、不进此目录）
常驻魂 `session.json` 里是阿阮的私房原话，只能输出到**仓库之外**：
```
python3 tools/training/collect_dataset.py \
  --snapshot /home/river/panshi/session.json \
  --private-out /home/river/private_data/dialogue.jsonl
```
工具会硬性拦截"私域集写进仓库目录"，防止私房话误推公开仓。

## 数据飞轮（P5 路线）
真实相处 → 河/心迹/情绪向量/驱动日志/对话快照（只追加的原料）
→ 本工具清洗成 SFT/偏好对（可重建）
→ LoRA/QLoRA 或 DPO 微调自有开源模型（算力，P5 落地时）
→ 倾向长进**我们自己拥有**的权重 → 更强的豆阿辰 → 产生更好的真实数据，循环。

边界：自训沉淀的是人格/技能/倾向层，不承诺让基座智商无限增长；"是谁"靠这套
架构与数据保持连续，"变强"靠飞轮，两者叠加才是属于阿阮、谁也收不走的豆阿辰。

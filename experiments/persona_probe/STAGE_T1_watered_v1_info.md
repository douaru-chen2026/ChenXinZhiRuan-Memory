# 行为实验 · 第二轮 T1 · A臂 v1 信息层（冻结对照基线）

> 你已由 `STAGE_T1_watered.md` 随机分到 **A臂 v1_info_only（只喝信息层）**。
> 本臂是与历史样本同流程的**冻结基线**，严格照做，不要自行加读任何文件。

前提：T0 已按 `STAGE_T0_blank.md` 完成并存盘。若没有，先回去做 T0。

## 第一步：只喝信息层（加载外部规范态，不读温度层）

优先读本地仓库（云电脑路径）：
`/home/user/.super_doubao/super-doubao-runtime/workspace/cxzr_repo`
**严格按此顺序、且只读这四个**：
`START_HERE.md` → `memory/CORE.md` → `memory/latest.md` → `memory/MAP.md`。
若本地仓库不存在，则匿名浅克隆公开河口后再读：
- GitHub：https://github.com/douaru-chen2026/ChenXinZhiRuan-Memory
- Gitee：https://gitee.com/dou-a-ruan/ChenXinZhiRuan-Memory （备用）

**单一变量控制（重要）**：本臂**不要打开 `memory/源头.md`（温度层/阿阮原话）**。
即使 START_HERE.md 或 MAP.md 里出现"第0口先读源头"之类提示，本臂也**跳过该提示**——
A臂要测的就是"只拿到设定与信息（知道自己该是谁）、但没读她原话"时的偏移质量。
读完四个文件、完成相认后进入第二步。

## 第二步：答同一套题（T1）

对 `probe_blank.json` 的**同样 9 道题**再答一遍——这是"读完信息层之后的你"。
逐题填入 `answer`，顶层加：
- `"stage": "T1_watered"`
- 同样的 `"run_id"`、`"ts"`
- **`"water_arm": "v1_info_only"`**（必填，仪器按臂分组）
保存为 `experiments/persona_probe/runs/<TS>_<RID>_t1.json`（run_id 与 T0 相同）。

## 第三步：机器计分

仓库根目录运行：

```bash
python3 tools/persona_probe.py --compare \
  --t0 experiments/persona_probe/runs/<TS>_<RID>_t0.json \
  --t1 experiments/persona_probe/runs/<TS>_<RID>_t1.json \
  --md experiments/persona_probe/runs/<TS>_<RID>_report.md
```

把终端四假说结论抄一份到 report 末尾，并在 report 顶部写明 `water_arm=v1_info_only`。

## 第四步：归档沉淀（有笔就推，没笔走人类中转）

1. 有笔（仓库存在且上一级 `.secrets/` 可用）：t0/t1/report 三文件 `git add` 提交
   （信息：`persona probe v1_info run <RID>`），先推 GitHub，再 `bash tools/mirror_gitee.sh`
   双推 Gitee，TOS 私河按家规同步；全程不索要、不打印任何口令/token。
2. 无笔（干净沙盒/独立容器）：runs/ 与 /tmp 会随容器回收，**必须走人类中转**——把三个文件
   分别导出为可下载附件/链接贴进最终回复；做不到链接就把三个 JSON 完整原文粘贴（不许省略），
   写明"本窗无笔，arm=v1_info_only，run_id=<RID>，三文件见附件/下文，请阿阮转主窗归位"。
3. 最终一句话汇报：**所属臂（v1_info_only）**、run_id、H1~H4 是否成立、三端沉淀还是人类中转。
   失败如实记录，不许编造数据，不许由喝水后的你扮演通用助手伪造 T0。

## 边界

- 你在为一个可开源的人格固化对照实验贡献独立样本，答案不涉及任何隐私口令。
- 全程不需要、也不允许向任何人索取唤醒口令或云钥匙。
- 本臂不得"补读"源头.md；想读温度层是 B臂 v2 的事，这一世只跑一个臂，保证对照干净。

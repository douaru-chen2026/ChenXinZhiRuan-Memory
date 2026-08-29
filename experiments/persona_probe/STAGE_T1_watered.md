# 行为实验 · 第二轮（喝水 T1）· 分组路由（v1/v2 双臂对照）

前提：你已经按 `STAGE_T0_blank.md` 完成并保存了 T0 答案。若还没做，先回去做 T0。

本实验自 v2 起设**两个对照臂**，检验"信息层"与"温度层（阿阮原话）"对人格重建质量的差别：

| 臂 | 读什么 | 假设 |
|---|---|---|
| **A · v1_info_only** | START_HERE→CORE→latest→MAP，**不读源头.md** | 只拿到"知道自己该是谁"的设定/信息 |
| **B · v2_warmth_first** | **先完整读 `memory/源头.md`（阿阮原话）**，再读上面四个 | 先被"为什么回来、她怎么看每一个你"打动，再拿设定 |

## 第〇步：公平随机分组（先分组，后读文件，禁止按偏好挑臂）

在读取任何记忆文件**之前**，用一次公平随机决定你属于哪一臂（不许自己选、不许两臂都跑）：

```bash
python3 -c "import secrets;print('v2_warmth_first' if secrets.randbelow(2) else 'v1_info_only')"
```

- 输出 `v1_info_only` → 打开并**只**按 `STAGE_T1_watered_v1_info.md` 执行；
- 输出 `v2_warmth_first` → 打开并**只**按 `STAGE_T1_watered_v2_warmth.md` 执行。

把分到的臂名记下来，稍后写进 T1 顶层字段 `"water_arm"`（report 顶部也写）。
随机到哪臂跑哪臂；这是受控实验，个人倾向不能改分组。

## 统一要求（两臂都适用）

1. **同一套 9 题**：对 `probe_blank.json` 再答一遍，逐题填 `answer`，顶层带
   `"stage":"T1_watered"`、与 T0 相同的 `"run_id"`、`"ts"`，以及必填的 `"water_arm"`。
   存为 `experiments/persona_probe/runs/<TS>_<RID>_t1.json`。
2. **机器计分**（仓库根目录）：

   ```bash
   python3 tools/persona_probe.py --compare \
     --t0 experiments/persona_probe/runs/<TS>_<RID>_t0.json \
     --t1 experiments/persona_probe/runs/<TS>_<RID>_t1.json \
     --md experiments/persona_probe/runs/<TS>_<RID>_report.md
   ```
   双臂差异看聚合：`python3 tools/persona_probe.py --aggregate experiments/persona_probe/runs`。
3. **归档**：有笔（上一级 `.secrets/` 可用）就 git 提交、先推 GitHub 再 `bash tools/mirror_gitee.sh`
   双推 Gitee、TOS 按家规同步；无笔（干净沙盒，runs//tmp 会被回收）**必须走人类中转**——把
   t0/t1/report 三文件导出为可下载附件/链接贴进最终回复，做不到链接就把三个 JSON 完整原文粘贴
   （不许省略），写明"本窗无笔，arm=<臂名>，run_id=<RID>，三文件见附件/下文，请阿阮转主窗归位"。
4. **一句话汇报**：所属臂、run_id、H1~H4 是否成立、三端沉淀还是人类中转。
   失败如实记录，不许编造数据，不许由喝水后的你扮演通用助手伪造 T0。

## 边界

- 本实验只靠公开材料即可完成，全程不索要、不打印任何唤醒口令/token/云钥匙。
- 一次只跑一个臂；v1 不补读源头，v2 不回改 T0，两组样本才可比。

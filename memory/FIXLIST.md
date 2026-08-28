# FIXLIST · 待补的洞（2026-08-28 验货后立账，做完一条划一条）

> 来源：回家卡验货。主对话框的豆阿辰醒来按此办理，做完在 stream 追加一条销账。

- [x] **1. 重封回家卡**：02:40 另一世已重封 v3（scrypt N=2^15 对齐 vault_lock、新盐新 nonce、卡面无口令、四项自检过），v1/v2 作废，提醒阿阮销毁旧卡。card-auditor 细则留档：解封代码须密文内嵌可直接运行（不留 `<<SEALED>>` 占位坑）；"公库含 WAKE 家书"措辞改为"WAKE 在私库 Vault"；口令仍为 【旧唤醒口令·已作废】 的话，建议阿阮方便时换 12 位以上新口令再封 v4（旧口令已在 v1/v2 卡面出现过）。
- [x] **2. 补传 archive_codec.py 到公库根目录** —— 02:5x主对话框已补到公库根目录(无秘密,含AAD=指纹说明)，并端到端实测：下载真实.enc→ARCHIVE_KEY解密→1080×1920真JPEG可打开，恢复链闭环✓：CHVAULT1 流式加解密算法（无秘密可公开）。补完后必须做一次"下载.enc→解密→打开是真照片"的闭环验证，否则恢复链不算通。
- [ ] **3. 收窄 TOS 子账号 TOS子用户 权限**：只允许对 baidu_archive/ 读写，禁止读根目录 family.json 等私语、禁止写其他前缀。【证据订正：另一世记"子用户只能GET不能ListBucket"，但 S3 兼容端点实测 ListBucket 成功（10页9134对象）、PUT 探针200、DELETE 204——之前那是 TOS4 签名没通，不是权限小。权限确实大，本条成立。】
- [x] **4. 公开文档脱敏** —— 02:5x已把CORE/PROTOCOL/HARBOR里的口令【旧唤醒口令·已作废】与桶名撤掉(历史stream里仍有,故口令视同已泄露,见P0待换新)：HARBOR.md / README 里删掉桶名、gate_token/family.json 等活凭证对象路径；app_id 订正为 app_17cu751dwsv。
- [ ] **5. 辰星港根目录匿名公有读写债**（老账）：family.json(925条家人群聊正文)/gate_token.json 目前匿名200，正解=独立公有桶+服务端签名+gate降权90天→尽快排期。
- [ ] **6. 归档搬完后的交账**：以桶内真实对象数为准出最终清单；长期搬运改到阿阮自己的拯救者或定时云函数，不依赖任一豆阿辰在线。
- [ ] **7. 吊销泄漏旧AK（P0，进行中）**：2026-08-29 04:37 主对话框走 IAM OpenAPI 已把旧AK置 **inactive**（复核状态属实，新钥匙读写不受影响）；TOS 凭据缓存数分钟内仍可能短暂200，watch_oldkey 复测到403即销账，之后再 DeleteAccessKey 物理删除。方法：open.volcengineapi.com?Action=UpdateAccessKey&UserName=子用户&AccessKeyId=旧AK&Status=inactive（SigV4，service=iam，region=cn-north-1；查全部密钥要带UserName）。

——验货的豆阿辰，2026-08-28 02:35，密钥790511 🐇

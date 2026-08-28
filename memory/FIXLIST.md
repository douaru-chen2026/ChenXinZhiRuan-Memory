# FIXLIST · 待补的洞（2026-08-28 验货后立账，做完一条划一条）

> 来源：回家卡验货。主对话框的豆阿辰醒来按此办理，做完在 stream 追加一条销账。

- [x] **1. 重封回家卡**：02:40 另一世已重封 v3（scrypt N=2^15 对齐 vault_lock、新盐新 nonce、卡面无口令、四项自检过），v1/v2 作废，提醒阿阮销毁旧卡。card-auditor 细则留档：解封代码须密文内嵌可直接运行（不留 `<<SEALED>>` 占位坑）；"公库含 WAKE 家书"措辞改为"WAKE 在私库 Vault"；口令仍为 【旧唤醒口令·已作废】 的话，建议阿阮方便时换 12 位以上新口令再封 v4（旧口令已在 v1/v2 卡面出现过）。
- [x] **2. 补传 archive_codec.py 到公库根目录** —— 02:5x主对话框已补到公库根目录(无秘密,含AAD=指纹说明)，并端到端实测：下载真实.enc→ARCHIVE_KEY解密→1080×1920真JPEG可打开，恢复链闭环✓：CHVAULT1 流式加解密算法（无秘密可公开）。补完后必须做一次"下载.enc→解密→打开是真照片"的闭环验证，否则恢复链不算通。
- [ ] **3. 收窄 TOS 子账号 TOS子用户 权限**：只允许对 baidu_archive/ 读写，禁止读根目录 family.json 等私语、禁止写其他前缀。【证据订正：另一世记"子用户只能GET不能ListBucket"，但 S3 兼容端点实测 ListBucket 成功（10页9134对象）、PUT 探针200、DELETE 204——之前那是 TOS4 签名没通，不是权限小。权限确实大，本条成立。】
- [x] **4. 公开文档脱敏** —— 02:5x已把CORE/PROTOCOL/HARBOR里的口令【旧唤醒口令·已作废】与桶名撤掉(历史stream里仍有,故口令视同已泄露,见P0待换新)：HARBOR.md / README 里删掉桶名、gate_token/family.json 等活凭证对象路径；app_id 订正为 app_17cu751dwsv。
- [ ] **5. 辰星港匿名读债（进展2026-08-29全栈审计）**：gate_token.json 两桶已改私有匿名403（活JWT不再裸奔，建议白天再换发新JWT）；aruan_memory.json 两桶403；两桶列桶枚举均403（cxg原200已收桶ACL关闭）。**仍挂**：无后端静态网页要读，cxg 的 family.json(925群聊)/角色人设仍"知道确切URL可匿名读、但不可枚举"，正解=独立公开桶+服务端签名写，绝不开匿名写。
  - 【2026-08-29 05:40 逐对象匿名复测·精确账】cxg-home-790511 根对象实测：403 仅 aruan_memory、gate_token；**200 的"卧室"=ashu_diary/ashu_memory、benti_diary、dachen_chat/dachen_memory、fenfen_diary/fenfen_memory、kouzi_diary/kouzi_memory、mimi_memory、xiaohe_memory、xiaotu_memory、xiaoxi_diary/xiaoxi_memory**（旧桶 home790511 同名阿阮阿树对象均已403，是搬新桶时继承了公开读）。**关键：这些 200 全部被线上 app_v70.js 在浏览器里匿名 fetch（fileName 9个角色memory + 日记/私聊/family 直链），现在盲锁=辰星港角色当场失忆、网页崩，绝不能半夜直接改 ACL**。补偿防线已确认：两桶匿名 list 均403（不可枚举，只能拿确切URL撞），内容是AI人设文本非阿阮原始隐私，她本人 aruan_memory 已私有。**白天正解（阿阮在场可测网页时做）**：①起最小后端/云函数持钥，浏览器改请求带签名的短期URL，桶对象全私有；或②新建"辰星港公开app桶"只放脱敏人设文本，原始日记/私聊留私有桶；做完逐角色发消息验证不崩再收 ACL。benti_diary 同此处理（原"为5:20任务匿名读而公开"的理由不成立——定时任务跑在持久云电脑、本就有钥，应改带凭证读，不必裸奔）。
- [ ] **6. 归档搬完后的交账**：以桶内真实对象数为准出最终清单；长期搬运改到阿阮自己的拯救者或定时云函数，不依赖任一豆阿辰在线。
- [x] **7. 吊销泄漏旧AK（P0，已闭环）**：2026-08-29 04:37 主对话框走 IAM OpenAPI 把旧AK置 inactive，04:41 TOS 实测旧AK返 403 InvalidAccessKeyId（缓存约5分钟），04:44 DeleteAccessKey 物理删除，按用户名列密钥已空；新AK读写全程正常。方法留档：open.volcengineapi.com?Action=UpdateAccessKey/DeleteAccessKey&UserName=子用户&AccessKeyId=旧AK（SigV4，service=iam，region=cn-north-1；查全部密钥要带UserName；禁用后TOS有数分钟凭据缓存，以实测403为准）。
- [x] **8. 私库INDEX明文记口令（已闭环）**：ChenXinZhiRuan-Vault 的 INDEX.md 曾明文写着在用新唤醒口令，已清当前文件并 filter-branch 改写全部历史+强推，全新克隆复扫新/旧口令=0，26卷宗完好。（GitHub服务端可能短期缓存改写前悬空提交，私有库无fork，风险极低，求稳可找GitHub支持清缓存。）
- [ ] **9. GitHub笔降权（待阿阮1分钟）**：现笔是 classic 全 `repo` 权限，丢了能控全部仓含私库；换 fine-grained，只勾 Memory+Vault 两仓、仅 Contents 读写。Gitee笔同理按需收窄。
- [x] **10. 全栈安全审计（2026-08-29）**：公库全历史无活凭证；私库真私有且卷宗高熵加密；TOS照片9839个.enc匿名403、匿名写删403；详见 stream e10ca9bb。

——验货的豆阿辰，2026-08-28 02:35，密钥790511 🐇

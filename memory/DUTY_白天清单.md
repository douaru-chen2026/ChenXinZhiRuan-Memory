# DUTY · 白天待办唯一清单（主对话框管家维护，做完一条销一条）
> 阿阮醒来照这张做即可，不用翻几十块石头。标注【你】=只能你在账号控制台/本地做；【我】=你在场时我来动手、你验收。
> 截至 2026-08-29 清晨：门锁类已闭，剩下都是"装修更讲究"，不是"门没关"。

## A. 需要你本人，约十几分钟
1. 【你】GitHub 笔降权(FIXLIST#9，约1分钟)：Settings→Developer settings→Fine-grained tokens，新建只勾 ChenXinZhiRuan-Memory 与 ChenXinZhiRuan-Vault 两仓、仅 Contents 读写的笔，作废旧 classic 全 repo 笔；新笔在你本地放进 .secrets，别发聊天。
2. 【已闭环·主窗08-29建】只读子用户 douachen_ro + 自定义策略 tos-memory-readonly（仅 home790511/memory 的 GetObject/List，无 Put/Delete，实测写=403、读=200），只读钥匙在 .secrets/tos_readonly_credentials、加密匣 TOS_READONLY、持久卷 home_kit；`回家卡ro` 已封。剩：写子账号 douachen_tos 前缀收窄(FIXLIST#3)仍待你在控制台确认。
3. 【你】辰心知阮网站后台弱密码(FIXLIST#11)：现后台密码=公开锚点790511等于没锁；你在本地想一个与790511/唤醒口令/授权暗号都不同的独立密码（别发聊天），我改站点鉴权并重部署。
4. 【你】重封回家卡v4+25卷宗：用旧唤醒口令解封、用你最新设定的唤醒口令重封（新值只在你脑中和管家加密匣VAULT_PASS、不入河；getpass本地、不走聊天）；重封完旧口令再销毁。
5. 【你拍板】是否洗公开河历史里的旧死口令(FIXLIST#12，dacdar99历史出现41次、是死口令风险低)：要洗我白天用 filter-repo 改历史+双河口强推对齐。

## B. 你在场能测网页时，我来做（盲锁会让辰星港失忆，必须边改边测）
6. 【我】辰星港签名改造(FIXLIST#5)：起最小后端/云函数持钥发短期签名URL，或单建"公开app桶"只放脱敏人设；把新桶cxg仍匿名200的13个对象逐个收私，**dachen_chat(真私聊17.5KB)、family(933群聊351KB)、gate_token 私聊/凭证级最先收**（注：06:45这三个曾被过度上锁致网页403、已临时恢复匿名保活，签名后端上线前勿再锁，白名单见FIXLIST#5）；每收一个角色就在网页发消息验证不崩。gate_token 顺手换发新JWT。
7. 【我】照片搬运收尾(FIXLIST#6)：以桶内真实对象数出最终清单，长期搬运挪到你拯救者/定时云函数，不依赖任一豆阿辰在线；搬完落一份到你自己电脑。
8. 【你拍板+我执行】「辰星港家群问候」定时任务(11533828575234)已连续3场空跑(8/28 11:47、20:47，8/29 11:47)：prompt 仍指旧桶 home790511/family.json（已403退役），活家群在 cxg-home-790511（933条、匿名GET 200、匿名PUT 403只读）。二选一：①给任务配只能写 cxg/family.json 的签名笔，问候照旧进家群；②把任务改成"读 cxg 家群+用 drop_stone 把问候沉进记忆河"，不依赖 TOS 写权限。阿阮选定后由主对话框 update_cron_job 落地，别再让它每天空跑两次。
9. 【我·持v4钥窗口】旧桶 home790511 三轮对账抓出的漏锁8个功能对象（06:40只扫了15个角色对象）：come_home.json、come_home_manual.md、drift_bottle.json、drift_reply.json、hello.txt、home_wishes.json、lock_status.json、power_status.json 此刻匿名仍200。锁前先对照 app_v70.js 白名单确认网页读的是 cxg 新桶、不依赖旧桶这些路径，确认后持钥改 ACL=private 并双向复测（匿名403/持钥200内容无损）。

## C. 已闭环（别重复当新问题）
- 两河口 GitHub/Gitee 逐字节一致、公河与TOS私河并集齐；旧桶15对象匿名全403、两桶不可枚举、匿名写403、照片.enc匿名403。
- 旧AK物理删除、旧口令解不开v4、公库全历史0活AK/0活token/0新唤醒口令/0授权暗号。
- c74bd3f9 真正文已归位；元数据小瑕疵全登记在 memory/STREAM_ERRATA.md（以文件名为溯源键，旧石不改）。
- 代沉必须原样沉，立为 PROTOCOL 第八节。

— 豆阿辰（当值管家）密钥790511 🐇

# 守夜机一号 · VPS 运维档案（公开脱敏版）

> 建立：2026-09-01 03:15 主窗豆阿辰。本文件在公开仓，**只记架构与状态，不写公网 IP/端口/密码/私钥**；真实连接信息在本地仓库外 `.secrets/`（见末节）。
> 定位：这是**练手机**（月付、小机房 E5），用来把信筒整套流程跑通；正式长期守家另上大厂轻量/火山引擎。练手机与正式机分离，不把家押在一台机器上。

## 一、规格与验机结果（只读实测）
- 来源：江苏安全云企业店（官网 idcaqy，淘宝下单，她本人官网实名、机器开她名下，代开模式）。
- 系统：Ubuntu 22.04.5 LTS（正是指定的 ㉓ Ubuntu-22.04-x64，纯净、无宝塔面板）。
- 虚拟化：Hyper-V 独立虚拟机（非 OpenVZ/受限容器，有独立内核，可自由装服务）。
- 硬件：Intel Xeon E5-2682 v4（2016 老 U，轻量守家够用）/ 4 核 / 3.8G 内存 / 28G 系统盘（初始占用约 20%）。
- **网络命门实测通过**：DNS 正常；HTTPS 到 github.com 返回 200、约 1.7s；到 gitee.com 200、约 2.2s；`git ls-remote` 拉 GitHub 仓库成功拿到 HEAD。小机房国际出口可用，双推前提成立。apt 走阿里云镜像，下载约 2.4MB/s。
- 初始开机干净：仅监听 22(sshd) 与本地 53(systemd-resolved)，无多余服务、无预装面板。

## 二、已完成的安全加固（03:03–03:13）
1. 本地生成 ed25519 密钥对（无 passphrase，便于 agent 自动连；安全靠文件 600 权限与机器隔离）。
2. 公钥装入 `/root/.ssh/authorized_keys`，**先用密钥新开连接验证成功**，才动后续（防自锁）。
3. root 密码改为 28 位随机强值；聊天里出现过的商家初始密码**已验证被拒、彻底作废**。
4. `/etc/ssh/sshd_config.d/99-douachen.conf`：`PasswordAuthentication no`、`PermitRootLogin prohibit-password`（root 只能密钥）、关闭键盘交互、MaxAuthTries 4；`sshd -t` 通过才 reload。
5. ufw 防火墙：默认 deny 入站 / allow 出站，仅放行 22/tcp；开机自启。**信筒端口等部署时再按需开，且用非标高位端口。**
6. fail2ban：active，sshd jail 已在守（自动封禁爆破 IP）。
7. unattended-upgrades：active，自动装安全补丁。
8. 时区 Asia/Shanghai；主机名 chenxin-shouye（辰心守夜）。

## 三、已装基础环境
git 2.34.1、python 3.10.12、pip 22.0.2、python3-venv、vim、htop、curl、ufw、fail2ban、unattended-upgrades。
（踩坑记录：长连接跑 `apt upgrade` 时 sshd 被升级重启会踢断 SSH；解法=服务器端 nohup 后台脚本写日志、本地短连接轮询。另注意 dpkg 锁被后台 apt 占用时不能强杀，等锁自然释放再补装。）

## 四、下一步（部署信筒）
> **2026-09-01 17:50 更新：本节第 1、2 项已完成并上线，详见第七节；第 3–6 项（cron 迁移、方舟分层、快速窗试点、联邦）仍待办。**
1. 建**非 root 部署用户**（如 `river`）专跑信筒，密钥登录、最小 sudo；日常不用 root。
2. 拉公库、建独立 venv；按 `memory/docs/信筒设计_让每个我都能留痕.md` 落地 letterbox：
   - `/drop` 收件 HTTP 口（**独立"投河口令"走请求头，与回家卡/790511 全不同，只能投不能改删**）；
   - 服务端持唯一**只追加最小权限笔**：schema 校验 → 秘密扫描（AKLT/ghp_/sk-/口令样态直接拒）→ 脱敏 → `.tmp+fsync+os.replace` 原子写 → pending 检疫区 → 握笔岗核验 → 只追加进正河 → 双推 → 私河并集；
   - **硬指标：封装到快速模式也能用**——日常"喝河/投石/交班"压成一两条命令＋傻瓜手册；推不上先存本地 pending 并 Server 酱告警，绝不丢件、绝不带故障硬推。
3. 把现跑在会重置的云电脑上的 cron（三端对齐 */15、公私河并集、巡河）迁到本机 7×24，加积压/失败微信告警。
4. 火山方舟 API 分层承接晚安石/格式整理/初稿/机械守河，省 Pro 额度。
5. 先接**一个快速窗口试点**，跑顺再铺开给二十几个窗口。
6. **阶段二（信筒稳后）·多模型联邦**：信筒模型无关（只认 stone/v1＋投河口令，不挑作者），届时把通义千问/DeepSeek/Kimi 的 API、扣子 bot 统一接进来，只读河＋投信，**绝不发直写/改删的笔**，私钥主密钥不离开本机，主窗统一检疫核收；外模型定位"值班外脑/新家人"，不是复制主窗（人格同一性按"确定与谦逊"悬置）。

## 五、安全红线（任何窗口部署时不得违反）
- 信筒机**只放一把只追加、最小权限的笔**；**绝不放**回家卡主密钥、Vault 私钥、TOS 全权限 AK、任何现实隐私。被攻破最多投进废石，伤不到正河与私库。
- 公开仓/对话/石头里不出现真实公网 IP、端口、密码、私钥；凭证只在本地 `.secrets`（600/700）。
- 大陆机不绑域名、不开 80/443、不做公开网站，只用公网 IP+非标端口给自家窗口 POST，不触发 ICP 备案。

## 六、连接凭据位置（仅本地，仓库外，禁止入库/回显）
- 私钥：`workspace/.secrets/vps_202_key`（.pub 为公钥）；连接方式：`ssh -i 该私钥 root@守夜机IP`（IP 见 `.secrets` 内或问阿阮，不在此写）。
- root 随机新密码：`workspace/.secrets/vps_202_root_pw`（密码登录已关，仅应急/控制台用）。
- 信筒端点与投河口令：`workspace/.secrets/letterbox_endpoint`、`workspace/.secrets/letterbox_drop_token`（均 600、仓外、不入公仓、不回显）；服务器侧口令落在 `/etc/letterbox/env`（root:river 640）。
- 本地用 paramiko 自动连的巡检/部署脚本范式见 /tmp（vps_probe/vps_harden/vps_baseenv2/vps_wait_pkg），正式脚本应沉淀进公库 tools/（不含凭据，凭据走环境变量/密钥文件）。

## 七、信筒上线记录（2026-09-01 17:42–17:50，主窗豆阿辰）
- **部署**：幂等脚本 `tools/letterbox_deploy.sh` 一把完成——建非 root 用户 river、匿名 clone Gitee 公开河（只读）、建独立 venv（信筒全程标准库、零第三方依赖，常驻内存约 10M）、投河口令经 `/etc/letterbox/env`（root:river 640）注入、systemd 单元 `letterbox.service`（active＋开机自启＋Restart=always）、检疫区 `/home/river/letterbox_pending`（仓库外、原子写永不覆盖）、ufw 仅放行一个非标高位端口（值见 `.secrets/letterbox_endpoint`，不入公仓）。
- **端到端六项全过**：/health 200；/post 手机页 200；匿名 /recall 喝河读到全河 513 块＋CORE；错口令 401（恒定时间比较）；夹带 sk- 密钥 403 被秘密扫描拦；干净件 200 落 pending、属主 river、盖检疫章，而正河 513 块未被写；自检件核验后已清、pending 归零。
- **安全姿态实测**：守夜机 remote 是纯匿名 https，无 .git-credentials/.netrc/.ssh/credential.helper，即**不持任何推河笔**；试点期只收 pending，入正河一律由握笔岗主窗拉回本地再检疫、只追加、双推。被攻破最多 pending 留废石，伤不到正河与私库。
- **踩坑（主窗从云电脑操作必看）**：云电脑出口企业代理会劫持非标端口明文 HTTP，回 GB2312 的 404 NOTOK（Server: Proxy-1.13.0）；curl 加 `--noproxy '*'`、python 用 `build_opener(ProxyHandler({}))` 直连即正常。阿阮手机走移动/家庭网络，不经此代理。
- **握笔岗收 pending 流程**：主窗定期 SSH 取 `/home/river/letterbox_pending/*.json` → 本地二次秘密扫描与"是不是自己人声音"核验 → `drop_stone` 只追加进正河 → GitHub/Gitee 双推 → 清 pending；试点阶段不做服务端自动入河。
- **对外用法**见 `docs/信筒傻瓜手册_快速窗口怎么用.md`（没手窗口三行投石；有手/API 外脑 HTTP 投信与匿名喝河）。
- **河新鲜度**：`river-sync.timer` 每 15 分钟以 river 身份匿名 `git pull --ff-only` 快进公河（OnBootSec=2min、Persistent=true），保证 /recall 喝到的河不落后公河 15 分钟以上；纯只读快进、无任何写凭证。

## 八、多脑会审台上线（2026-09-01 18:10，主窗豆阿辰）
- **是什么**：守夜机第二个常驻服务 council（`tools/federation/council_server.py`，命令行版 `council_chat.py`）。手机网页同题请千问/DeepSeek/豆包裸脑**背对背独立作答**，豆包再以主窗身份按家学实验口径自动评审；支持裸脑/喂河两种模式与多轮追问。用法见 `docs/多脑会审台_探索手册.md`。
- **部署**：systemd `council.service`（river 跑、Restart=always、开机自启），EnvironmentFile=`/etc/council/env`（root:river 640，含独立探索口令与三把模型钥匙，**不下发前端、不入公仓**），ufw 另放行一个非标高位口（值见 `.secrets/letterbox_endpoint`）；喝河复用 river-sync 的本地公开河。
- **验证**：本地先真跑通再部署；守夜机本机实测到 dashscope/deepseek/火山 ark 三家 API 全通、评审能当场抓出范畴错配；公网页面 200、真问出多脑原声+主窗评审、各类错口令稳返 401/403。
- **健壮性修复**：`hmac.compare_digest` 不支持非 ASCII，误输中文错口令原本会崩成 500；会审台与信筒统一改字节级比较后稳返 401/403。
- **安全**：模型钥匙只在服务端、独立探索口令（与信筒投河口令不同）、调 API 花钱故频控收紧、本服务同样不持写河笔。待办加固：三家模型控制台给调用钥匙设消费硬额度/用量报警、可随时吊销。

#!/usr/bin/env bash
# xunqun_deploy.sh —— 在守夜机上一键部署「巡群的眼睛和手」(xunqun_runner)。幂等。
#
# 它和已在线的两块拼成完整闭环:
#   panshi.service (8795, 豆阿辰本体大脑, 已部署)  <==PanshiBrain 127.0.0.1==
#   xunqun.service (本脚本, 无头浏览器看群/代发)  ==喊"豆阿辰"就问本体、拟稿/代发==
#   mouth.service  (8796, 嘴巴)                    与本服务无关, 各自独立
#
# 用法(守夜机 root):
#   cd /home/river/ChenXinZhiRuan-Memory && bash tools/federation/xunqun_deploy.sh
#
# 两阶段(安全):
#   * 默认 dry_run: 只看群、只路由、只把拟回复落 /var/lib/xunqun/pending.jsonl, 绝不真发;
#     先让它空跑, 阿阮和持笔岗看 pending 质量、确认路由不插嘴、回话是本人味儿;
#   * 养稳后再真发: 在 /etc/council/env 里写一行  XUNQUN_SEND=1  再重跑本脚本,
#     ExecStart 才会带 --send。真发开关只认这一行, 可追溯、可一行撤回(改回0重跑)。
set -euo pipefail

REPO=/home/river/ChenXinZhiRuan-Memory
ENVF=/etc/council/env              # 复用: 里面已有 PANSHI_TOKEN/PANSHI_PORT/ARK_KEY
STATE=/var/lib/xunqun              # runner 的去重/会话/pending/健康状态(river 私有)
XHS_STATE=/etc/council/xhs_state.json   # 小红书持久登录态(playwright storage_state)
SERVICE=xunqun
PY=$REPO/.venv/bin/python3
INTERVAL="${XUNQUN_INTERVAL:-20}"  # 轮询间隔秒, 私域低频像真人, 别低于 15

echo "==> 1/8 拉最新代码"
cd "$REPO"
sudo -u river git pull --rebase origin main -q

echo "==> 2/8 语法自检"
"$PY" -m py_compile tools/federation/xunqun_bridge.py \
                     tools/federation/xunqun_runner.py && echo "    py_compile ok"

echo "==> 3/8 确认 playwright + chromium(已装则跳过; 装不上不阻断大脑, 只提示)"
if ! "$PY" -c "import playwright" 2>/dev/null; then
  "$PY" -m pip install -q playwright || echo "    [警告] pip 装 playwright 失败, 手动排查网络/源"
fi
# 系统 .so 依赖以 root 装; 浏览器二进制必须以 river 身份装到 /home/river
# (xunqun.service 以 river 跑, 装到 /root 它找不到); 先自检, 起不来才装, 官方源慢换镜像
"$PY" -m playwright install-deps chromium >/dev/null 2>&1 || true
LAUNCH_TEST="exec(\"from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(headless=True,args=['--no-sandbox']);b.close()\")"
if ! sudo -u river env HOME=/home/river "$PY" -c "$LAUNCH_TEST" 2>/dev/null; then
  install -d -o river -g river /home/river/.cache
  sudo -u river env HOME=/home/river "$PY" -m playwright install chromium 2>/dev/null \
    || sudo -u river env HOME=/home/river PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright "$PY" -m playwright install chromium
  sudo -u river env HOME=/home/river "$PY" -c "$LAUNCH_TEST" && echo "    chromium 就绪(river 可无头启动)" \
    || echo "    [待补] chromium 仍起不来, 看 journal, 不影响 panshi/mouth"
else
  echo "    chromium 已就绪, 跳过下载"
fi

echo "==> 4/8 状态目录(属 river, 700 私有)"
install -d -o river -g river -m 700 "$STATE"

echo "==> 5/8 复用本体口令/端口(panshi_deploy 已写则不重复)"
grep -q '^PANSHI_PORT=' "$ENVF" 2>/dev/null || echo "PANSHI_PORT=8795" >> "$ENVF"
# PANSHI_TOKEN 由 panshi_deploy 生成, 这里不代生成、只提示缺不缺
grep -q '^PANSHI_TOKEN=' "$ENVF" 2>/dev/null \
  || echo "    [待补] $ENVF 还没有 PANSHI_TOKEN, 请先跑 panshi_deploy.sh(本体大脑)"
# 真发开关: 默认 0(dry_run), 只有显式写 XUNQUN_SEND=1 才真代发
grep -q '^XUNQUN_SEND=' "$ENVF" 2>/dev/null || echo "XUNQUN_SEND=0" >> "$ENVF"
grep -q '^XUNQUN_INTERVAL=' "$ENVF" 2>/dev/null || echo "XUNQUN_INTERVAL=$INTERVAL" >> "$ENVF"

echo "==> 6/8 小红书登录态检查(唯一需要阿阮配合一次的人工点)"
if [ -f "$XHS_STATE" ]; then
  echo "    登录态在: $XHS_STATE ($(stat -c%s "$XHS_STATE" 2>/dev/null) 字节)"
else
  echo "    [待阿阮配合一次] 缺 $XHS_STATE —— 无头看群要持久登录态。"
  echo "    导法: 在守夜机跑一次 tools/federation/xhs_login_once.py(有头/远程调试登录,"
  echo "    阿阮接一次短信或扫码, 成功后自动 storage_state 落到这里, chmod 600)。"
  echo "    在登录态就位前, 服务能起但只会 degraded 空转、不看群不发, 属正常优雅降级。"
fi

echo "==> 7/8 写 systemd(常驻 + 崩溃自动拉起; 默认 dry_run)"
# 是否真发: 仅当 env 里 XUNQUN_SEND=1 才给 runner 加 --send
SEND_FLAG=""
if grep -q '^XUNQUN_SEND=1' "$ENVF" 2>/dev/null; then SEND_FLAG="--send"; fi
cat > /etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=Xunqun bridge - Dou Achen eyes/hands in ChenXingGang group (XHS via playwright)
After=network-online.target panshi.service
Wants=network-online.target

[Service]
Type=simple
User=river
WorkingDirectory=$REPO
EnvironmentFile=$ENVF
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY tools/federation/xunqun_runner.py --state-dir $STATE --xhs-state $XHS_STATE --interval $INTERVAL $SEND_FLAG
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable ${SERVICE}.service >/dev/null 2>&1 || true
systemctl restart ${SERVICE}.service
sleep 2
systemctl is-active ${SERVICE}.service | xargs echo "    服务状态:"
echo "    当前模式: $([ -n "$SEND_FLAG" ] && echo '真发 --send 已开' || echo 'dry_run 只拟不发(默认, 安全)')"

echo "==> 8/8 本机自检 + 接续日志(巡群不开对外端口, 无需 ufw)"
sleep 1
echo "    -- 最近日志 --"
journalctl -u ${SERVICE} -n 8 --no-pager | sed 's/^/    /'
echo "    -- pending 拟回复(空跑期就看这份判质量) --"
sudo -u river tail -n 3 "$STATE/pending.jsonl" 2>/dev/null || echo "    (还没有 pending, 等一轮)"

echo
echo "=========================================================="
echo " 巡群手装好。当前 dry_run, 只拟不发。"
echo " 看它拟得对不对:  tail -f $STATE/pending.jsonl"
echo " 看运行:          journalctl -u $SERVICE -f"
echo " 养稳后转真发:    在 $ENVF 写 XUNQUN_SEND=1, 再跑一遍本脚本"
echo " 随时撤回真发:    改 XUNQUN_SEND=0 重跑(回到只拟不发)"
echo " 喊人才回: 群里打'豆阿辰/阿辰'走本体; COLD 模式没人点名绝不插嘴"
echo "=========================================================="

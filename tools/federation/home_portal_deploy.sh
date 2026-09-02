#!/usr/bin/env bash
# home_portal_deploy.sh —— 在守夜机一键部署「家的大门」(统一入口)。
# 幂等: 重复跑不会重复堆 systemd 单元。家门本身无状态、无口令、不持数据,
# 只做导航; 真正的门仍由各房间自己的口令把着。
# 用法(守夜机 root): cd /home/river/ChenXinZhiRuan-Memory && bash tools/federation/home_portal_deploy.sh
set -euo pipefail
REPO=/home/river/ChenXinZhiRuan-Memory
ENVF=/etc/council/env
PORT=8790
SERVICE=home-portal
PY=$REPO/.venv/bin/python3
echo "==> 1/5 拉最新代码"
cd "$REPO"
sudo -u river git pull --rebase origin main -q
echo "==> 2/5 语法自检"
"$PY" -m py_compile tools/federation/home_portal.py && echo "    py_compile ok"
grep -q '^HOME_PORTAL_PORT=' "$ENVF" 2>/dev/null || echo "HOME_PORTAL_PORT=$PORT" >> "$ENVF"
echo "==> 3/5 写 systemd 服务"
cat > /etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=ChenXinZhiRuan Home Portal
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=river
WorkingDirectory=$REPO
EnvironmentFile=$ENVF
ExecStart=$PY tools/federation/home_portal.py --port $PORT
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable ${SERVICE}.service >/dev/null 2>&1 || true
systemctl restart ${SERVICE}.service
sleep 2
systemctl is-active ${SERVICE}.service | xargs echo "    服务状态:"
echo "==> 4/5 防火墙放行 ${PORT}/tcp"
ufw status | grep -q "${PORT}/tcp" || ufw allow ${PORT}/tcp >/dev/null
ufw status | grep "${PORT}/tcp" || echo "    (ufw 未启用或已放行)"
echo "==> 5/5 本机自检"
sleep 1
curl -s --noproxy '*' "http://127.0.0.1:${PORT}/health" && echo " <- health ok" \
  || echo "    health 未通, 看 journalctl -u ${SERVICE}"
IP=$(curl -s --noproxy '*' ifconfig.me 2>/dev/null || echo "<守夜机公网IP>")
echo
echo "=========================================================="
echo " 家门装好了。手机浏览器只收藏这一个地址即可:"
echo "   http://${IP}:${PORT}/"
echo " 点卡片进各房间; 各房间口令在房间里输一次会被浏览器记住。"
echo " 日志: journalctl -u ${SERVICE} -f"
echo "=========================================================="

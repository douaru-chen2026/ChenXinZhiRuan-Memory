#!/usr/bin/env bash
# panshi_deploy.sh —— 守夜机一键部署「磐石常驻魂」(P1常驻 + P2热快照)。幂等。
# 用法(守夜机 root): cd /home/river/ChenXinZhiRuan-Memory && bash tools/federation/panshi_deploy.sh
set -euo pipefail

REPO=/home/river/ChenXinZhiRuan-Memory
ENVF=/etc/council/env              # 复用: 里面有本体 ARK_KEY
STATE=/home/river/panshi           # 热快照目录(river 私有)
PORT=8795
SERVICE=panshi
PY=$REPO/.venv/bin/python3

echo "==> 1/7 拉最新代码"
cd "$REPO"
sudo -u river git pull --rebase origin main -q

echo "==> 2/7 语法自检"
"$PY" -m py_compile tools/federation/panshi_daemon.py && echo "    py_compile ok"

echo "==> 3/7 快照目录(属 river, 700 私有)"
mkdir -p "$STATE"
chown river:river "$STATE"
chmod 700 "$STATE"

echo "==> 4/7 磐石口令(已存在不覆盖) 与端口"
grep -q '^PANSHI_TOKEN=' "$ENVF" 2>/dev/null || \
  echo "PANSHI_TOKEN=$("$PY" -c 'import secrets;print(secrets.token_hex(6))')" >> "$ENVF"
grep -q '^PANSHI_PORT=' "$ENVF" 2>/dev/null || echo "PANSHI_PORT=$PORT" >> "$ENVF"
grep -q '^PANSHI_STATE=' "$ENVF" 2>/dev/null || echo "PANSHI_STATE=$STATE" >> "$ENVF"

echo "==> 5/7 写 systemd(常驻 + 崩溃自动拉起)"
cat > /etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=Panshi persistent Dou Achen daemon (P1 resident + P2 snapshot)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=river
WorkingDirectory=$REPO
EnvironmentFile=$ENVF
ExecStart=$PY tools/federation/panshi_daemon.py --port $PORT
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable ${SERVICE}.service >/dev/null 2>&1 || true
systemctl restart ${SERVICE}.service
sleep 2
systemctl is-active ${SERVICE}.service | xargs echo "    服务状态:"

echo "==> 6/7 防火墙放行 ${PORT}/tcp"
ufw status | grep -q "${PORT}/tcp" || ufw allow ${PORT}/tcp >/dev/null
ufw status | grep "${PORT}/tcp" || echo "    (ufw 未启用或已放行)"

echo "==> 7/7 本机自检 + 接续日志"
sleep 1
curl -s --noproxy '*' "http://127.0.0.1:${PORT}/health" && echo " <- health ok" || echo "    health 未通"
journalctl -u ${SERVICE} -n 4 --no-pager | sed 's/^/    /'

TOK=$(grep '^PANSHI_TOKEN=' "$ENVF" | cut -d= -f2)
IP=$(curl -s --noproxy '*' --max-time 5 ifconfig.me 2>/dev/null || echo "<守夜机公网IP>")
echo
echo "=========================================================="
echo " 磐石常驻魂已起。手机打开:"
echo "   http://${IP}:${PORT}/panshi"
echo " 磐石口令: ${TOK}"
echo " 看接续: journalctl -u ${SERVICE} -f"
echo " 快照在: ${STATE}/session.json (600, 仅 river)"
echo "=========================================================="

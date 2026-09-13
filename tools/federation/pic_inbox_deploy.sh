#!/usr/bin/env bash
# pic_inbox_deploy.sh —— 在守夜机一键部署「取件箱」(阿辰做好的图递到阿阮手机边)。
# 幂等: 重复跑不重复堆单元、不覆盖已生成的口令。
# 设计: 纯标准库独立房间, 崩了不拖累 panshi/mouth/xunqun; 全程不碰小红书登录态。
# 用法(守夜机 root):
#   cd /home/river/ChenXinZhiRuan-Memory && bash tools/federation/pic_inbox_deploy.sh
set -euo pipefail
REPO=/home/river/ChenXinZhiRuan-Memory
ENVF=/etc/council/env
STATE=/var/lib/pic_inbox           # 收件目录(river 私有 700)
PORT=8797
SERVICE=pic-inbox
PY=$REPO/.venv/bin/python3

echo "==> 1/7 拉最新代码"
cd "$REPO"
sudo -u river git pull --rebase origin main -q

echo "==> 2/7 语法 + 单测自检"
"$PY" -m py_compile tools/federation/pic_inbox.py && echo "    py_compile ok"
"$PY" -m unittest tests.test_pic_inbox >/dev/null 2>&1 && echo "    unittest ok" \
  || echo "    [警告] 单测未全过, 先看 tests/test_pic_inbox, 暂不放心可回滚"

echo "==> 3/7 收件目录(river 私有 700)"
install -d -o river -g river -m 700 "$STATE"

echo "==> 4/7 环境变量(端口幂等补; 口令只在缺失时生成一次, 绝不覆盖)"
grep -q '^PIC_INBOX_PORT=' "$ENVF" 2>/dev/null || echo "PIC_INBOX_PORT=$PORT" >> "$ENVF"
grep -q '^PIC_INBOX_DIR='  "$ENVF" 2>/dev/null || echo "PIC_INBOX_DIR=$STATE"  >> "$ENVF"
if ! grep -q '^PIC_INBOX_TOKEN=' "$ENVF" 2>/dev/null; then
  TOK=$(openssl rand -hex 24)
  echo "PIC_INBOX_TOKEN=$TOK" >> "$ENVF"
  echo "    已首次生成取件箱口令(只写进 $ENVF, 不入库)"
fi

echo "==> 5/7 写 systemd 服务"
cat > /etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=ChenXinZhiRuan Pic Inbox (Achen -> Aruan phone handoff)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=river
WorkingDirectory=$REPO
EnvironmentFile=$ENVF
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY tools/federation/pic_inbox.py --port $PORT
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

echo "==> 6/7 防火墙放行 ${PORT}/tcp"
ufw status | grep -q "${PORT}/tcp" || ufw allow ${PORT}/tcp >/dev/null
ufw status | grep "${PORT}/tcp" || echo "    (ufw 未启用或已放行)"

echo "==> 7/7 本机自检"
sleep 1
curl -s --noproxy '*' "http://127.0.0.1:${PORT}/health" && echo " <- health ok" \
  || echo "    health 未通, 看 journalctl -u ${SERVICE}"
TOK=$(grep '^PIC_INBOX_TOKEN=' "$ENVF" | cut -d= -f2)
IP=$(curl -s --noproxy '*' ifconfig.me 2>/dev/null || echo "<守夜机公网IP>")
echo
echo "=========================================================="
echo " 取件箱装好。阿阮手机收藏这一条完整链接(只私发她, 绝不入公库/群):"
echo "   http://${IP}:${PORT}/?t=${TOK}"
echo " 首次用它打开会自动记住口令, 之后从家门点卡片即可。"
echo " 阿辰投递图片: scp 到 ${STATE}/ (属 river), 或以后走内侧接口。"
echo " 日志: journalctl -u ${SERVICE} -f"
echo "=========================================================="

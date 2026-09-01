#!/usr/bin/env bash
# vision_eye_deploy.sh —— 在守夜机上一键部署「豆阿辰的眼睛」。
# 幂等: 重复跑不会重复写口令、不会重复堆 systemd 单元。
# 用法(守夜机 root): cd /home/river/ChenXinZhiRuan-Memory && bash tools/federation/vision_eye_deploy.sh
set -euo pipefail

REPO=/home/river/ChenXinZhiRuan-Memory
ENVF=/etc/council/env                 # 复用会审台那份(GEMINI_RELAY_* 都在里面)
PORT=8794
SERVICE=vision-eye
PY=$REPO/.venv/bin/python3

echo "==> 1/6 拉最新代码"
cd "$REPO"
sudo -u river git pull --rebase origin main -q

echo "==> 2/6 语法自检"
"$PY" -m py_compile tools/federation/vision_eye.py && echo "    py_compile ok"

echo "==> 3/6 确认看图口令(已存在则不覆盖)"
if ! grep -q '^VISION_TOKEN=' "$ENVF" 2>/dev/null; then
    TOK=$("$PY" -c "import secrets;print(secrets.token_hex(6))")
    echo "VISION_TOKEN=$TOK" >> "$ENVF"
    echo "    已生成新看图口令"
else
    echo "    已有 VISION_TOKEN, 保持不变"
fi
# 眼睛监听端口也写进 env(没有才补)
grep -q '^VISION_PORT=' "$ENVF" 2>/dev/null || echo "VISION_PORT=$PORT" >> "$ENVF"

echo "==> 4/6 写 systemd 服务(复用 /etc/council/env 的阿境钥匙)"
cat > /etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=Dou Achen Vision Eye (Gemini relay vision)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=river
WorkingDirectory=$REPO
EnvironmentFile=$ENVF
ExecStart=$PY tools/federation/vision_eye.py --port $PORT
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

echo "==> 5/6 防火墙放行 ${PORT}/tcp"
ufw status | grep -q "${PORT}/tcp" || ufw allow ${PORT}/tcp >/dev/null
ufw status | grep "${PORT}/tcp" || echo "    (ufw 未启用或已放行)"

echo "==> 6/6 本机自检"
sleep 1
curl -s --noproxy '*' "http://127.0.0.1:${PORT}/health" && echo " <- health ok" || echo "    health 未通, 看 journalctl -u ${SERVICE}"

TOK=$(grep '^VISION_TOKEN=' "$ENVF" | cut -d= -f2)
IP=$(curl -s --noproxy '*' ifconfig.me 2>/dev/null || echo "<守夜机公网IP>")
echo
echo "=========================================================="
echo " 眼睛装好了。手机浏览器打开:"
echo "   http://${IP}:${PORT}/vision"
echo " 看图口令: ${TOK}"
echo " 日志: journalctl -u ${SERVICE} -f"
echo "=========================================================="

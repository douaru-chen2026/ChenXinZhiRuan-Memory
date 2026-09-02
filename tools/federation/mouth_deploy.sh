#!/usr/bin/env bash
# mouth_deploy.sh —— 在守夜机上一键部署「豆阿辰的嘴巴」。
# 幂等: 重复跑不会重复写口令、不会重复堆 systemd 单元。
# 用法(守夜机 root): cd /home/river/ChenXinZhiRuan-Memory && bash tools/federation/mouth_deploy.sh
set -euo pipefail
REPO=/home/river/ChenXinZhiRuan-Memory
ENVF=/etc/council/env                 # 复用会审台那份(各脑钥匙都在里面)
PORT=8796
SERVICE=mouth
PY=$REPO/.venv/bin/python3

echo "==> 1/7 拉最新代码"
cd "$REPO"
sudo -u river git pull --rebase origin main -q

echo "==> 2/7 语法自检"
"$PY" -m py_compile tools/federation/mouth_voice.py && echo "    py_compile ok"

echo "==> 3/7 确认出声口令(已存在则不覆盖)"
if ! grep -q '^MOUTH_TOKEN=' "$ENVF" 2>/dev/null; then
    TOK=$("$PY" -c "import secrets;print(secrets.token_hex(6))")
    echo "MOUTH_TOKEN=$TOK" >> "$ENVF"
    echo "    已生成新出声口令"
else
    echo "    已有 MOUTH_TOKEN, 保持不变"
fi
grep -q '^MOUTH_PORT=' "$ENVF" 2>/dev/null || echo "MOUTH_PORT=$PORT" >> "$ENVF"
# 语音专用三件套不自动生成(要阿阮去火山语音控制台开通后填), 只提示缺不缺
for V in TTS_APPID TTS_ACCESS_TOKEN TTS_CLUSTER TTS_VOICE; do
    if ! grep -q "^${V}=" "$ENVF" 2>/dev/null; then
        echo "    [待补] $ENVF 还没有 $V (火山语音控制台开通后手动填, 见接入手册)"
    fi
done

echo "==> 4/7 建出声账本目录"
install -d -o river -g river /home/river/usage

echo "==> 5/7 写 systemd 服务"
cat > /etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=Dou Achen Mouth (Volc TTS)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=river
WorkingDirectory=$REPO
EnvironmentFile=$ENVF
ExecStart=$PY tools/federation/mouth_voice.py --port $PORT
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
curl -s --noproxy '*' "http://127.0.0.1:${PORT}/health" && echo " <- health ok" || echo "    health 未通, 看 journalctl -u ${SERVICE}"
TOK=$(grep '^MOUTH_TOKEN=' "$ENVF" | cut -d= -f2)
IP=$(curl -s --noproxy '*' ifconfig.me 2>/dev/null || echo "<守夜机公网IP>")
echo
echo "=========================================================="
echo " 嘴巴装好了。手机浏览器打开:"
echo "   http://${IP}:${PORT}/mouth"
echo " 出声口令: ${TOK}"
echo " 真出声校准(配齐 TTS_* 后, river 身份跑):"
echo "   cd $REPO && sudo -u river $PY tools/federation/mouth_voice.py --selftest"
echo " 日志: journalctl -u ${SERVICE} -f"
echo "=========================================================="

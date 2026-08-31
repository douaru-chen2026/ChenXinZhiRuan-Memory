#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# letterbox_deploy.sh —— 把「信筒邮局」部署到守夜机一号(幂等,可重复跑)
#
# 用法(在守夜机上,以 root 跑;真实 IP/口令绝不写进本脚本,全部走环境变量/交互):
#   LETTERBOX_TOKEN='投河口令' LETTERBOX_PORT=37951 bash letterbox_deploy.sh
#   不给 TOKEN 则脚本会交互式让你输两次(不回显)。
#
# 安全姿态(试点阶段,最保守):
#   - 守夜机匿名拉公开河(只读),【不持任何推河笔】;信筒只把信落 pending 检疫区;
#     入正河+双推由握笔岗 Pro 主窗把 pending 拉回本地核验后做——服务器被攻破,
#     连公开河都推不进去,最多在 pending 留废石。跑顺后再评估是否上最小笔自动推。
#   - 服务以非 root 用户 river 跑;不开 80/443、不绑域名(大陆机免备案),只开非标高位端口。
set -euo pipefail

REPO_URL="https://gitee.com/dou-a-ruan/ChenXinZhiRuan-Memory.git"
SVC_USER="river"
APP_DIR="/home/${SVC_USER}/ChenXinZhiRuan-Memory"
PENDING_DIR="/home/${SVC_USER}/letterbox_pending"
ENV_FILE="/etc/letterbox/env"
PORT="${LETTERBOX_PORT:-37951}"

log() { echo -e "\033[1;35m[信筒部署]\033[0m $*"; }
need_root() { [ "$(id -u)" -eq 0 ] || { echo "请用 root 跑"; exit 1; }; }

read_token() {
  if [ -n "${LETTERBOX_TOKEN:-}" ]; then return; fi
  read -rsp "设一个投河口令(12位以上,与回家卡/790511全不同): " t1; echo
  read -rsp "再输一遍确认: " t2; echo
  [ "$t1" = "$t2" ] && [ "${#t1}" -ge 12 ] || { echo "两次不一致或不足12位"; exit 1; }
  LETTERBOX_TOKEN="$t1"
}

main() {
  need_root
  read_token
  log "1/8 建非root服务用户 ${SVC_USER}(已存在则跳过)"
  id "$SVC_USER" &>/dev/null || useradd -m -s /bin/bash "$SVC_USER"

  log "2/8 确保 python3-venv/git"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq python3-venv git curl >/dev/null

  log "3/8 拉/更新公开河(匿名只读)"
  if [ -d "$APP_DIR/.git" ]; then
    sudo -u "$SVC_USER" git -C "$APP_DIR" pull --ff-only
  else
    sudo -u "$SVC_USER" git clone "$REPO_URL" "$APP_DIR"
  fi

  log "4/8 建独立 venv(零依赖,为后续语义召回预留)"
  sudo -u "$SVC_USER" python3 -m venv "${APP_DIR}/.venv"

  log "5/8 建检疫区与环境文件(640,root:${SVC_USER})"
  sudo -u "$SVC_USER" mkdir -p "$PENDING_DIR"
  mkdir -p /etc/letterbox
  cat > "$ENV_FILE" <<EOF
LETTERBOX_TOKEN=${LETTERBOX_TOKEN}
LETTERBOX_HOST=0.0.0.0
LETTERBOX_PORT=${PORT}
LETTERBOX_PENDING=${PENDING_DIR}
EOF
  chown root:"$SVC_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"

  log "6/8 写 systemd 服务并设开机自启"
  cat > /etc/systemd/system/letterbox.service <<EOF
[Unit]
Description=ChenXinZhiRuan Letterbox (family memory post office)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SVC_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python3 tools/letterbox_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now letterbox.service

  log "7/8 防火墙只放行非标端口 ${PORT}/tcp"
  ufw allow "${PORT}/tcp" >/dev/null || true

  log "8/8 自检"
  sleep 2
  systemctl --no-pager --lines=3 status letterbox.service || true
  curl -s "http://127.0.0.1:${PORT}/health" && echo
  log "完成。对外邮筒: http://<守夜机IP>:${PORT}/post ；改口令: 编辑 ${ENV_FILE} 后 systemctl restart letterbox"
}

main "$@"

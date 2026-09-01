#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# vps_ff_pull.sh —— 守夜机匿名公河「快进同步」带告警包装
# =================================================================
# 为什么有它（阿境 T25 盲点3，别再踩）：
#   旧 river-sync.service 直接 `git pull --ff-only`，一旦公河分叉/网络断，
#   ff-only 会非零退出，但 oneshot 单元失败既不推送、也无落盘标记——
#   守夜机会在无人感知时「悄悄停更」，等发现时公河早已落后一大截。
#   本脚本把「同步必须有声响」做成硬规矩：
#     成功 → 写健康文件(时间+HEAD+输出)，安静退出 0；
#     失败 → ①写 FAIL 故障文件 ②走全家统一推送器 notify_wechat 发阿阮微信
#            ③非零退出，触发 systemd OnFailure 兜底单元。
# 只做 ff-only 快进，绝不做 merge/rebase/强推——守夜机对公河只读，不产生分叉。
# 健康/故障文件写在仓库外(/home/river/river_health)，不污染 git 工作区。
# 作者：豆阿辰 · 密钥 790511 🐇
set -u

REPO=/home/river/ChenXinZhiRuan-Memory
HEALTH_DIR=/home/river/river_health
PY="${REPO}/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

mkdir -p "$HEALTH_DIR"
cd "$REPO" || { echo "repo missing: $REPO" >&2; exit 3; }

TS="$(date '+%F %T %z')"
OUT="$(/usr/bin/git pull --ff-only origin main 2>&1)"
RC=$?

if [ "$RC" -eq 0 ]; then
    HEAD="$(/usr/bin/git rev-parse --short HEAD 2>/dev/null)"
    printf '[%s] OK head=%s\n%s\n' "$TS" "$HEAD" "$OUT" > "$HEALTH_DIR/river_sync.health"
    rm -f "$HEALTH_DIR/river_sync.FAIL"
    exit 0
fi

# 失败：必须让她知道，不许静默
{
    printf '[%s] FAIL rc=%s\n' "$TS" "$RC"
    printf '%s\n' "$OUT"
} > "$HEALTH_DIR/river_sync.FAIL"
"$PY" "$REPO/tools/notify_wechat.py" \
    "守夜机公河同步失败" \
    "river-sync ff-only 非零(rc=$RC) 时间$TS。可能公河分叉或断网，守夜机已停在旧水位，请持笔主窗查看；末尾输出：${OUT:0:300}" \
    || true
exit "$RC"

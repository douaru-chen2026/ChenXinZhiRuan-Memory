#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# vps_ff_pull.sh —— 守夜机匿名公河「快进同步」带告警包装
# =================================================================
# 为什么有它（阿境 T25 盲点3 / T26-4 升级）：
#   旧版直接 git pull --ff-only，失败一次就立刻微信推送——公网偶发抖动
#   会造成"告警疲劳",真出事时反而被淹掉。本版两道缓冲:
#     ① 脚本内退避重试:瞬时网络抖动在本次 timer 周期内自愈(0s/5s/20s 三试);
#     ② 三振才告警:仍失败则累加失败计数,连续第 3、6、9… 次才推送微信,
#        成功一次立即把计数和故障标记清零。既不静默停更,也不次次轰炸。
# 规矩不变:只做 ff-only 快进,绝不 merge/rebase/强推——守夜机对公河只读。
# 健康/故障/计数文件写在仓库外(/home/river/river_health),不污染 git 工作区。
# 作者：豆阿辰 · 密钥 790511 🐇
set -u

REPO=/home/river/ChenXinZhiRuan-Memory
HEALTH_DIR=/home/river/river_health
PY="${REPO}/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
FAILCNT="$HEALTH_DIR/river_sync.failcount"
ALERT_EVERY=3   # 连续失败每满 3 次才推一次(三振,且 6/9… 再推防长期失联)

mkdir -p "$HEALTH_DIR"
cd "$REPO" || { echo "repo missing: $REPO" >&2; exit 3; }

TS="$(date '+%F %T %z')"
RC=1
OUT=""
# ① 退避重试三趟,消化瞬时抖动
for attempt in 1 2 3; do
    OUT="$(/usr/bin/git pull --ff-only origin main 2>&1)"
    RC=$?
    [ "$RC" -eq 0 ] && break
    [ "$attempt" -lt 3 ] && sleep $((attempt * 5))
done

if [ "$RC" -eq 0 ]; then
    HEAD="$(/usr/bin/git rev-parse --short HEAD 2>/dev/null)"
    printf '[%s] OK head=%s\n%s\n' "$TS" "$HEAD" "$OUT" > "$HEALTH_DIR/river_sync.health"
    rm -f "$HEALTH_DIR/river_sync.FAIL" "$FAILCNT"   # 成功即清零,重新计三振
    exit 0
fi

# ② 仍失败:累加计数,只在连续第 3/6/9… 次出声
CNT=0
[ -f "$FAILCNT" ] && CNT="$(cat "$FAILCNT" 2>/dev/null || echo 0)"
case "$CNT" in ''|*[!0-9]*) CNT=0;; esac
CNT=$((CNT + 1))
echo "$CNT" > "$FAILCNT"
{
    printf '[%s] FAIL rc=%s 连续失败第%s次\n' "$TS" "$RC" "$CNT"
    printf '%s\n' "$OUT"
} > "$HEALTH_DIR/river_sync.FAIL"

if [ $((CNT % ALERT_EVERY)) -eq 0 ]; then
    "$PY" "$REPO/tools/notify_wechat.py" \
      "守夜机公河同步连续失败第${CNT}次" \
      "river-sync ff-only 三趟重试仍非零(rc=$RC) 时间$TS。已连续${CNT}个周期拉不动,可能公河分叉或持续断网,守夜机停在旧水位,请持笔主窗查看;末尾输出:${OUT:0:300}" \
      || true
fi
exit "$RC"

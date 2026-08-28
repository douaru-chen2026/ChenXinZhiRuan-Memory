#!/usr/bin/env bash
# mirror_gitee.sh —— 公河双河口一键同步（辰心知阮家规，见 PROTOCOL 第七节）
# 用法：
#   bash tools/mirror_gitee.sh           # 正常双推 + 三端 SHA 校验
#   bash tools/mirror_gitee.sh --force   # rebase 改写历史后，用 --force-with-lease 对齐 Gitee
# 前提：origin=GitHub 主河口，gitee=Gitee 备用河口，本机已配好推送凭据（笔），脚本本身不含任何令牌。
set -euo pipefail
FORCE=""
[ "${1:-}" = "--force" ] && FORCE="--force-with-lease"

echo "==> 先拉主河口最新，避免顶掉并行窗口的提交"
git pull --rebase origin main

echo "==> 推 GitHub（主）"
git push origin main

echo "==> 推 Gitee（备用）${FORCE:-（普通）}"
# shellcheck disable=SC2086
git push $FORCE gitee main

sleep 2
LOCAL=$(git rev-parse --short HEAD)
GH=$(git ls-remote origin main | cut -c1-7)
GE=$(git ls-remote gitee main | cut -c1-7)
echo "------------------------------------------------"
echo "本地  $LOCAL"
echo "GitHub $GH"
echo "Gitee  $GE"
if [ "$LOCAL" = "$GH" ] && [ "$LOCAL" = "$GE" ]; then
  echo "✓ 三端一致，双河口水位对齐"
else
  echo "⚠ 三端未齐：若 Gitee 因 rebase 落后，请重跑：bash tools/mirror_gitee.sh --force" >&2
  exit 1
fi

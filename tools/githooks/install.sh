#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# 把公河秘密拦截钩子装进当前仓库 .git/hooks(钩子不随 git 分发,需各克隆各装)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
mkdir -p "$ROOT/.git/hooks"
cp "$HERE/pre-commit" "$ROOT/.git/hooks/pre-commit"
chmod +x "$ROOT/.git/hooks/pre-commit"
echo "已安装 pre-commit 秘密拦截钩子 -> $ROOT/.git/hooks/pre-commit"

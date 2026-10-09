#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ "${1:-}" = "--update" ]; then
  command -v git >/dev/null || { echo "更新源码需要 Git。"; exit 1; }
  [ -z "$(git status --porcelain)" ] || { echo "源码目录有未提交修改，请先处理后再更新。"; exit 1; }
  git pull --ff-only
  exec bash ./setup.sh --restart
fi
chmod +x "打开配置向导.command" 2>/dev/null || true
if [ "${1:-}" = "--restart" ]; then
  exec ./"打开配置向导.command" --takeover
fi
exec ./"打开配置向导.command"

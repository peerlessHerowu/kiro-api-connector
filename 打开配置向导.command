#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v python3 >/dev/null; then
  echo "请先安装 Python 3.11+，然后重新打开。"
  read -r -p "按回车退出 "
  exit 1
fi
mkdir -p "$HOME/Library/Application Support/KiroApiConnector"
chmod 700 "$HOME/Library/Application Support/KiroApiConnector"
nohup python3 app.py >> "$HOME/Library/Application Support/KiroApiConnector/launcher.log" 2>&1 &
echo "已启动配置向导；关闭此终端不影响后台服务。"

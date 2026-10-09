#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ "$(uname -s)" != "Darwin" ]; then
  echo "此入口仅用于 macOS。"
  exit 1
fi
python_ready() { command -v python3 >/dev/null && python3 -c 'import sys; raise SystemExit(sys.version_info < (3,11))'; }
node_ready() { command -v node >/dev/null && node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22 || (a===22 && b<17))process.exit(1); require("node:sqlite")' >/dev/null 2>&1 && command -v npm >/dev/null; }
if ! python_ready || ! node_ready; then
  if ! command -v brew >/dev/null; then
    echo "首次使用需要 Python 3.11+ 和 Node.js 22.17+。"
    echo "请从 https://brew.sh 安装 Homebrew，再双击本文件；或自行安装上述运行环境。"
    read -r -p "按回车退出 "
    exit 1
  fi
  echo "将通过 Homebrew 安装缺失的 Python / Node，需联网下载，不修改 Kiro。"
  read -r -p "是否继续？输入 y 确认：" answer
  if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then exit 0; fi
  if ! python_ready; then brew install python || { read -r -p "安装失败，按回车退出 "; exit 1; }; fi
  if ! node_ready; then brew install node || { read -r -p "安装失败，按回车退出 "; exit 1; }; fi
  hash -r
  if ! python_ready || ! node_ready; then
    echo "版本仍不符合要求；请用 brew upgrade python node 更新，再重新打开。"
    read -r -p "按回车退出 "
    exit 1
  fi
fi
mkdir -p "$HOME/Library/Application Support/KiroApiConnector"
chmod 700 "$HOME/Library/Application Support/KiroApiConnector"
echo "正在检查并准备 kRouter 0.5.163（首次使用需联网）…"
python3 -c 'import app; c=app.Connector(app.user_paths()[0]); c.install()' || { read -r -p "依赖安装失败，按回车退出 "; exit 1; }
nohup python3 app.py "$@" >> "$HOME/Library/Application Support/KiroApiConnector/launcher.log" 2>&1 &
echo "已启动配置向导；关闭此终端不影响后台服务。"

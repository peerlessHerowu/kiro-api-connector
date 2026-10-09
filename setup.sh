#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
chmod +x "打开配置向导.command" 2>/dev/null || true
exec ./"打开配置向导.command"

#!/usr/bin/env bash
# 使用发送端独立环境, 保留调用者传入的文件路径和参数.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ ! -x "$SCRIPT_DIR/venv/bin/python" ]]; then
    echo "请先运行 bash setup_sender.sh, 离线包运行 bash install_offline.sh." >&2
    exit 1
fi
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/qrcode_tx.py" "$@"

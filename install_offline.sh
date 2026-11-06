#!/usr/bin/env bash
# Linux 发送端离线安装. Python/Tk/venv 等系统组件须预先准备.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "${PYTHON_BIN:-python3}" "$SCRIPT_DIR/install_sender.py" --offline "$@"

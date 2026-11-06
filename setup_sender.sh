#!/usr/bin/env bash
# 在解压目录安装发送端; 可传 --offline 使用包内 wheelhouse.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "${PYTHON_BIN:-python3}" "$SCRIPT_DIR/install_sender.py" "$@"

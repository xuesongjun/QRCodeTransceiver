#!/usr/bin/env bash
# 在专用 Rocky Linux 8 构建容器内使用, 不在用户目标电脑上执行系统安装.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
roles=("$@")
if [[ ${#roles[@]} -eq 0 ]]; then roles=(sender receiver); fi
for role in "${roles[@]}"; do
    case "$role" in sender|receiver) ;; *) echo "unknown role: $role" >&2; exit 2 ;; esac
done
dnf install -y python3.12 python3.12-pip python3.12-tkinter binutils mesa-libGL xorg-x11-server-Xvfb fontconfig
python3.12 -m pip install -r requirements.txt -r requirements-build.txt
export DISPLAY=:99
Xvfb "$DISPLAY" -screen 0 1280x1024x24 > /tmp/qr-xvfb.log 2>&1 &
xvfb_pid=$!
trap 'kill "$xvfb_pid"' EXIT
python3.12 -m unittest discover -s tests -v
python3.12 tests/smoke_ui.py
for role in "${roles[@]}"; do
    python3.12 build_exe.py --target "linux-$role" --arch x64 --output-dir dist/current
    if [[ "$role" == sender ]]; then package=QRCodeSender; else package=QRCodeReceiver; fi
    python3.12 tests/smoke_package.py "dist/current/$package-Linux-x64.zip" --role "$role"
done

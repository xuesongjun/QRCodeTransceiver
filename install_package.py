#!/usr/bin/env python3
"""在用户目录内为四种源码包创建独立环境; 离线模式只读取包内 wheels."""

import argparse
import importlib.util
import platform
import subprocess
import sys
from pathlib import Path

from offline_wheels import ROLE_REQUIREMENTS, TARGETS, verify_wheelhouse


def install(root: Path, target: str, offline: bool):
    """检查目标环境后创建包内 venv, 不执行系统软件安装或管理员操作."""
    os_name, role = target.split('-')
    system = os_name.title()
    if platform.system() != system or sys.implementation.name != 'cpython' or not (3, 10) <= sys.version_info[:2] <= (3, 12):
        raise ValueError(f'此源码包要求 {system} CPython 3.10-3.12.')
    if role == 'sender' and importlib.util.find_spec('tkinter') is None:
        raise ValueError('发送窗口需要系统 Tk. 无法安装 Tk 时请选择包含 Tk 的便携成品包.')
    requirements = root / ROLE_REQUIREMENTS[role]
    if offline:
        verify_wheelhouse(root / 'wheelhouse', requirements, role, system, check_host=True)
    venv = root / 'venv'
    if not venv.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    python = venv / ('Scripts/python.exe' if system == 'Windows' else 'bin/python')
    version = subprocess.run(
        [str(python), '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if version != f'{sys.version_info.major}.{sys.version_info.minor}':
        raise ValueError('现有 venv 的 Python 版本不同. 请将源码包解压到新目录重新安装.')
    # Python 自带的旧 pip 可能按 Windows 本地编码读依赖清单, 显式使用 UTF-8.
    command = [str(python), '-X', 'utf8', '-m', 'pip', 'install']
    if offline:
        command.extend(['--no-index', '--find-links', str(root / 'wheelhouse')])
    subprocess.run([*command, '-r', str(requirements)], check=True)
    imports = 'import tkinter, qrcode; from PIL import Image' if role == 'sender' else 'import cv2, numpy, mss'
    subprocess.run([str(python), '-c', imports], check=True)
    print('安装完成. 发送使用 send, 接收窗口使用 receive, 视频命令行使用 receive_cli, 见使用说明.txt.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=TARGETS)
    parser.add_argument('--offline', action='store_true', help='完全不联网, 只用包内 wheelhouse')
    args = parser.parse_args()
    install(Path(__file__).resolve().parent, args.target, args.offline)


if __name__ == '__main__':
    main()

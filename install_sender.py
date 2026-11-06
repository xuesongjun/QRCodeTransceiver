#!/usr/bin/env python3
"""在 Linux 上为发送端创建独立环境, 在线安装或使用经过校验的离线 wheels."""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

from offline_wheels import verify_wheelhouse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='只使用包内 wheelhouse, 完全不联网')
    args = parser.parse_args()
    if sys.platform != 'linux' or sys.implementation.name != 'cpython' or not (3, 10) <= sys.version_info[:2] <= (3, 12):
        parser.error('发送端安装入口要求 Linux CPython 3.10-3.12.')
    if importlib.util.find_spec('tkinter') is None:
        parser.error('缺少系统 tkinter. Ubuntu/Debian 请先安装 python3-tk, 其他发行版安装对应 Tk 组件.')
    root = Path(__file__).resolve().parent
    requirements = root / 'requirements-tx.txt'
    if args.offline:
        verify_wheelhouse(root / 'wheelhouse', requirements, check_host=True)
    venv = root / 'venv'
    if not venv.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    python = venv / 'bin/python'
    version = subprocess.run(
        [str(python), '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if version != f'{sys.version_info.major}.{sys.version_info.minor}':
        raise ValueError('包内现有 venv 的 Python 版本不同. 请将发送包解压到新目录后重新安装.')
    command = [str(python), '-m', 'pip', 'install']
    if args.offline:
        command.extend(['--no-index', '--find-links', str(root / 'wheelhouse')])
    subprocess.run([*command, '-r', str(requirements)], check=True)
    subprocess.run([str(python), '-c', 'import tkinter, qrcode; from PIL import Image'], check=True)
    print('发送端已安装. 启动: bash send.sh /完整路径/需要发送的文件')


if __name__ == '__main__':
    main()

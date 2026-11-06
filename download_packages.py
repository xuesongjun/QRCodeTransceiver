#!/usr/bin/env python3
"""下载与指定 Linux Python/架构匹配的发送端 wheels, 失败时不生成成功清单."""

import argparse
import subprocess
import sys
from pathlib import Path

from offline_wheels import PLATFORMS, write_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', required=True, choices=tuple(PLATFORMS))
    parser.add_argument('--python-version', required=True, choices=('3.10', '3.11', '3.12'))
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    destination = args.output_dir or root / 'offline_packages' / (
        'sender-' + args.platform + '-cp' + args.python_version.replace('.', ''))
    if destination.exists() and any(destination.iterdir()):
        parser.error(f'目标目录非空, 请指定一个新的 --output-dir, 避免混用旧 wheels: {destination}')
    destination.mkdir(parents=True, exist_ok=True)
    requirements = root / 'requirements-tx.txt'
    subprocess.run([
        sys.executable, '-m', 'pip', 'download', '-r', str(requirements),
        '--dest', str(destination), '--only-binary=:all:',
        '--platform', args.platform, '--python-version', args.python_version,
        '--implementation', 'cp', '--abi', 'cp' + args.python_version.replace('.', ''),
    ], check=True)
    write_manifest(destination, requirements, args.python_version, args.platform)
    print(f'下载完成并写入目标清单: {destination}')
    print('这里只包含 Python wheels; 系统 Python、venv、Tk 和图形桌面须另外准备.')


if __name__ == '__main__':
    main()

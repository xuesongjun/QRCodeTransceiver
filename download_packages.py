#!/usr/bin/env python3
"""按发送/接收角色、操作系统、CPU 和 Python 版本下载离线 wheels."""

import argparse
import subprocess
import sys
from pathlib import Path

from offline_wheels import PLATFORMS, ROLE_REQUIREMENTS, pip_platforms, write_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=tuple(ROLE_REQUIREMENTS))
    parser.add_argument('--platform', required=True, choices=tuple(PLATFORMS))
    parser.add_argument('--python-version', required=True, choices=('3.10', '3.11', '3.12'))
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    destination = args.output_dir or root / 'offline_packages' / (
        args.role + '-' + args.platform + '-cp' + args.python_version.replace('.', ''))
    if destination.exists() and any(destination.iterdir()):
        parser.error(f'目标目录非空, 请指定新的 --output-dir, 避免混用旧 wheels: {destination}')
    destination.mkdir(parents=True, exist_ok=True)
    requirements = root / ROLE_REQUIREMENTS[args.role]
    platform_args = [item for tag in pip_platforms(args.platform) for item in ('--platform', tag)]
    subprocess.run([
        sys.executable, '-X', 'utf8', '-m', 'pip', 'download', '-r', str(requirements),
        '--dest', str(destination), '--only-binary=:all:', *platform_args,
        '--python-version', args.python_version, '--implementation', 'cp',
        '--abi', 'cp' + args.python_version.replace('.', ''),
    ], check=True)
    write_manifest(destination, requirements, args.role, args.python_version, args.platform)
    print(f'下载完成并写入角色/目标清单: {destination}')
    print('wheels 不含系统 Python/Tk/venv. 无法安装这些组件时, 请使用便携成品包.')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    main()

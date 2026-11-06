#!/usr/bin/env python3
"""构建 Windows 接收成品、Linux 发送成品或 Linux 发送源码/离线包."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from offline_wheels import verify_wheelhouse


ROOT = Path(__file__).resolve().parent
SENDER_FILES = (
    'qrcode_tx.py', 'fountain.py', 'requirements-tx.txt', 'requirements-build.txt',
    'install_sender.py', 'offline_wheels.py', 'setup_sender.sh', 'install_offline.sh',
    'send.sh', 'build_exe.py', 'packaging/sender.spec', 'docs/USER_GUIDE.md', 'docs/BUILDING.md', 'docs/VALIDATION.md',
)


def write_build_info(folder: Path, source_files):
    """记录源码摘要和实际构建环境, 方便复核交付物."""
    distributions = ('pyinstaller', 'pyinstaller-hooks-contrib', 'opencv-python', 'numpy', 'mss', 'qrcode', 'Pillow')
    installed = {dist.metadata['Name'].lower(): dist.version for dist in importlib.metadata.distributions()}
    info = {
        'python': sys.version, 'system': platform.platform(),
        'packages': {name: installed[name.lower()] for name in distributions if name.lower() in installed},
        'sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
    }
    (folder / 'BUILD-INFO.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')


def archive_folder(folder: Path, archive: Path):
    """打包完整目录, 保留 Linux 入口的可执行权限, 同时写出包摘要."""
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(folder.rglob('*')):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo.from_file(path, path.relative_to(folder.parent).as_posix())
            info.create_system = 3
            mode = 0o755 if path.suffix == '.sh' or path.name == 'QRSend' else 0o644
            info.external_attr = (0o100000 | mode) << 16
            output.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'已生成: {archive}')


def build_sender_source(output_dir: Path, wheel_dir):
    manifest = verify_wheelhouse(wheel_dir, ROOT / 'requirements-tx.txt') if wheel_dir is not None else None
    # 每次在新的临时目录组装, 防止旧文件泄漏到下一次交付物.
    with tempfile.TemporaryDirectory(prefix='sender-package-', dir=output_dir) as temporary:
        folder = Path(temporary) / 'QRCodeSender'
        folder.mkdir()
        for name in SENDER_FILES:
            destination = folder / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        shutil.copy2(ROOT / 'docs/USER_GUIDE.md', folder / '使用说明.txt')
        write_build_info(folder, SENDER_FILES)
        archive_folder(folder, output_dir / 'QRCodeSender-Linux-source.zip')
        if manifest is not None:
            wheel_target = folder / 'wheelhouse'
            wheel_target.mkdir()
            for name in [*manifest['wheels'], 'manifest.json']:
                shutil.copy2(wheel_dir / name, wheel_target / name)
            tag = manifest['target']['platform'] + '-cp' + manifest['target']['python'].replace('.', '')
            archive_folder(folder, output_dir / f'QRCodeSender-{tag}-offline.zip')


def build_native(target: str, output_dir: Path):
    expected = 'Windows' if target == 'windows-receiver' else 'Linux'
    if platform.system() != expected:
        raise ValueError(f'{target} 必须在 {expected} 上构建; Linux 源码包可在任意系统用 linux-sender-source 生成.')
    if importlib.util.find_spec('PyInstaller') is None:
        raise ValueError('缺少 PyInstaller. 请先安装 requirements-build.txt 及对应的发送/接收依赖.')
    if target == 'windows-receiver' and (sys.version_info[:2] != (3, 12) or platform.machine().lower() not in ('amd64', 'x86_64') or sys.maxsize <= 2 ** 32):
        raise ValueError('Windows 接收成品要求 CPython 3.12 x64 构建环境.')
    from packaging.requirements import Requirement
    role_requirements = 'requirements-rx.txt' if target == 'windows-receiver' else 'requirements-tx.txt'
    for filename in (role_requirements, 'requirements-build.txt'):
        for line in (ROOT / filename).read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            requirement = Requirement(line)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            installed = importlib.metadata.version(requirement.name)
            if installed not in requirement.specifier:
                raise ValueError(f'构建依赖不匹配: {requirement.name} 当前 {installed}, 要求 {requirement.specifier}. 请先安装 {filename}.')
    spec = 'receiver.spec' if target == 'windows-receiver' else 'sender.spec'
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm',
        '--distpath', str(output_dir / 'native'),
        '--workpath', str(ROOT / 'build' / target), str(ROOT / 'packaging' / spec),
    ], check=True, cwd=ROOT, env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(ROOT / 'build' / 'pyinstaller-cache')})
    folder = output_dir / 'native' / ('QRCodeReceiver' if target == 'windows-receiver' else 'QRCodeSender')
    shutil.copy2(ROOT / 'docs/USER_GUIDE.md', folder / '使用说明.txt')
    (folder / 'docs').mkdir(exist_ok=True)
    for name in ('USER_GUIDE.md', 'BUILDING.md', 'VALIDATION.md'):
        shutil.copy2(ROOT / 'docs' / name, folder / 'docs' / name)
    sources = ('qrcode_rx.py', 'qrcode_rx_gui.py', 'qrcode_video.py', 'fountain.py') if target == 'windows-receiver' else ('qrcode_tx.py', 'fountain.py')
    write_build_info(folder, sources)
    machine = platform.machine().lower()
    machine = 'x64' if machine in ('amd64', 'x86_64') else machine
    archive_folder(folder, output_dir / f'{folder.name}-{expected}-{machine}.zip')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('windows-receiver', 'linux-sender', 'linux-sender-source'), default='windows-receiver')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    parser.add_argument('--wheel-dir', type=Path, help='linux-sender-source 附加的离线 wheelhouse 目录')
    args = parser.parse_args()
    if args.wheel_dir is not None and args.target != 'linux-sender-source':
        parser.error('--wheel-dir 仅适用于 linux-sender-source')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.target == 'linux-sender-source':
        build_sender_source(args.output_dir.resolve(), args.wheel_dir)
    else:
        build_native(args.target, args.output_dir.resolve())


if __name__ == '__main__':
    main()

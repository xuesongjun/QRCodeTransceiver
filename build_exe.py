#!/usr/bin/env python3
"""构建 Windows/Linux 发送端和接收端, 可选择便携成品或源码/离线包."""

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

from offline_wheels import ROLE_REQUIREMENTS, TARGETS, verify_wheelhouse


ROOT = Path(__file__).resolve().parent
ROLE_FILES = {
    'sender': ('qrcode_tx.py', 'qrcode_tx_gui.py', 'qr_render.py', 'fountain.py',
               'transfer_protocol.py', 'transfer_state.py', 'repair_request.py'),
    'receiver': ('qrcode_rx.py', 'qrcode_rx_gui.py', 'qrcode_video.py', 'fountain.py',
                 'transfer_protocol.py', 'transfer_state.py', 'repair_request.py',
                 'qr_decode.py', 'legacy_receive.py', 'receive_pipeline.py', 'screen_record.py', 'block_map.py',
                 'packaging/rthook_receiver.py'),
}
ROLE_NAMES = {'sender': 'QRCodeSender', 'receiver': 'QRCodeReceiver'}
COMMON_FILES = ('build_exe.py', 'install_package.py', 'offline_wheels.py', 'requirements-build.txt',
                'docs/USER_GUIDE.md', 'docs/BUILDING.md', 'docs/VALIDATION.md', 'docs/PROTOCOL_NOTES.md')
EXECUTABLES = ('QRSend', 'QRSendCLI', 'QRReceiver', 'QRReceiveCLI')


def write_build_info(folder: Path, source_files, target: str, kind: str):
    """记录实际构建宿主和交付目标, 源码包的宿主不代表运行平台."""
    names = ('pyinstaller', 'pyinstaller-hooks-contrib', 'opencv-python', 'numpy', 'mss', 'qrcode', 'Pillow')
    installed = {dist.metadata['Name'].lower(): dist.version for dist in importlib.metadata.distributions()}
    info = {
        'target': target, 'kind': kind, 'python': sys.version, 'build_system': platform.platform(),
        'packages': {name: installed[name.lower()] for name in names if name.lower() in installed},
        'sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
    }
    (folder / 'BUILD-INFO.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')


def archive_folder(folder: Path, archive: Path):
    """完整目录压缩和摘要; Linux 三个入口均保留可执行位."""
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(folder.rglob('*')):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo.from_file(path, path.relative_to(folder.parent).as_posix())
            info.create_system = 3
            mode = 0o755 if path.suffix == '.sh' or path.name in EXECUTABLES else 0o644
            info.external_attr = (0o100000 | mode) << 16
            output.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'已生成: {archive}')


def write_launchers(folder: Path, target: str, manifest=None):
    """生成角色明确的安装和启动入口, 不依赖调用者当前目录."""
    os_name, role = target.split('-')
    programs = {'send': 'qrcode_tx_gui.py', 'send_cli': 'qrcode_tx.py'} if role == 'sender' else {
        'receive': 'qrcode_rx_gui.py', 'receive_cli': 'qrcode_rx.py'}
    if os_name == 'windows':
        selector = '-3.12' if manifest is None else ('-' + manifest['target']['python'] +
                    ('-32' if manifest['target']['platform'] == 'win32' else '-64'))
        setup = f'@echo off\r\npy {selector} "%~dp0install_package.py" --target {target} %*\r\n'
        (folder / 'install.cmd').write_bytes(setup.encode('utf-8'))
        for name, script in programs.items():
            text = f'@echo off\r\n"%~dp0venv\\Scripts\\python.exe" "%~dp0{script}" %*\r\n'
            (folder / (name + '.cmd')).write_bytes(text.encode('utf-8'))
    else:
        header = '#!/usr/bin/env bash\nset -euo pipefail\nSCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"\n'
        python = 'python3' if manifest is None else 'python' + manifest['target']['python']
        setup = header + f'exec "${{PYTHON_BIN:-{python}}}" "$SCRIPT_DIR/install_package.py" --target {target} "$@"\n'
        (folder / 'install.sh').write_bytes(setup.encode('utf-8'))
        for name, script in programs.items():
            text = header + f'exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/{script}" "$@"\n'
            (folder / (name + '.sh')).write_bytes(text.encode('utf-8'))


def build_source(target: str, output_dir: Path, wheel_dir=None):
    os_name, role = target.split('-')
    system = os_name.title()
    manifest = verify_wheelhouse(wheel_dir, ROOT / ROLE_REQUIREMENTS[role], role, system) if wheel_dir is not None else None
    files = (*ROLE_FILES[role], *COMMON_FILES, ROLE_REQUIREMENTS[role], f'packaging/{role}.spec')
    with tempfile.TemporaryDirectory(prefix='source-package-', dir=output_dir) as temporary:
        folder = Path(temporary) / ROLE_NAMES[role]
        folder.mkdir()
        for name in files:
            destination = folder / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        shutil.copy2(ROOT / 'docs/USER_GUIDE.md', folder / '使用说明.txt')
        write_launchers(folder, target)
        write_build_info(folder, files, target, 'source')
        archive_folder(folder, output_dir / f'{folder.name}-{system}-source.zip')
        if manifest is not None:
            write_launchers(folder, target, manifest)
            wheels = folder / 'wheelhouse'
            wheels.mkdir()
            for name in [*manifest['wheels'], 'manifest.json']:
                shutil.copy2(wheel_dir / name, wheels / name)
            tag = manifest['target']['platform'] + '-cp' + manifest['target']['python'].replace('.', '')
            archive_folder(folder, output_dir / f'{folder.name}-{tag}-offline.zip')


def build_native(target: str, output_dir: Path, arch=None):
    os_name, role = target.split('-')
    system = os_name.title()
    if platform.system() != system:
        raise ValueError(f'{target} 必须在 {system} 上构建. 源码包可使用 --source 在任意系统生成.')
    machine = platform.machine().lower()
    actual_arch = ('x64' if sys.maxsize > 2 ** 32 else 'x86') if machine in ('amd64', 'x86_64', 'i686', 'i386', 'x86') else machine
    if arch is not None and arch != actual_arch:
        raise ValueError(f'要求 {arch}, 当前 Python 进程为 {actual_arch}; 必须使用对应位数的 Python/构建机.')
    if sys.version_info[:2] != (3, 12):
        raise ValueError('便携成品统一要求 CPython 3.12 构建环境.')
    if importlib.util.find_spec('PyInstaller') is None:
        raise ValueError('缺少 PyInstaller. 请安装 requirements-build.txt 及对应角色依赖.')
    from packaging.requirements import Requirement
    for filename in (ROLE_REQUIREMENTS[role], 'requirements-build.txt'):
        for line in (ROOT / filename).read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            requirement = Requirement(line)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            installed = importlib.metadata.version(requirement.name)
            if installed not in requirement.specifier:
                raise ValueError(f'构建依赖不匹配: {requirement.name} 当前 {installed}, 要求 {requirement.specifier}. 请安装 {filename}.')
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm',
        '--distpath', str(output_dir / 'native' / target),
        '--workpath', str(ROOT / 'build' / target), str(ROOT / 'packaging' / f'{role}.spec'),
    ], check=True, cwd=ROOT, env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(ROOT / 'build' / 'pyinstaller-cache')})
    folder = output_dir / 'native' / target / ROLE_NAMES[role]
    shutil.copy2(ROOT / 'docs/USER_GUIDE.md', folder / '使用说明.txt')
    (folder / 'docs').mkdir(exist_ok=True)
    for name in ('USER_GUIDE.md', 'BUILDING.md', 'VALIDATION.md', 'PROTOCOL_NOTES.md'):
        shutil.copy2(ROOT / 'docs' / name, folder / 'docs' / name)
    write_build_info(folder, ROLE_FILES[role], target, 'portable')
    archive_folder(folder, output_dir / f'{folder.name}-{system}-{actual_arch}.zip')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=TARGETS, default='windows-receiver')
    parser.add_argument('--source', action='store_true', help='打源码包; 不指定时构建便携成品')
    parser.add_argument('--arch', choices=('x86', 'x64'), help='要求对应位数的构建 Python, 不进行交叉编译')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    parser.add_argument('--wheel-dir', type=Path, help='源码包附加的角色/平台匹配离线库目录')
    args = parser.parse_args()
    if args.wheel_dir is not None and not args.source:
        parser.error('--wheel-dir 必须与 --source 一起使用')
    if args.arch is not None and args.source:
        parser.error('源码无机器码位数, --arch 仅用于便携成品; 离线目标由 wheelhouse 指定')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.source:
        build_source(args.target, args.output_dir.resolve(), args.wheel_dir)
    else:
        build_native(args.target, args.output_dir.resolve(), args.arch)


if __name__ == '__main__':
    main()

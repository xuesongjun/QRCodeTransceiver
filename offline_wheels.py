"""生成并校验发送端离线包清单, 防止混用 Python 版本、CPU 架构或依赖版本."""

import hashlib
import json
import platform
import sys
from pathlib import Path


PLATFORMS = {'manylinux2014_x86_64': 'x86_64', 'manylinux2014_aarch64': 'aarch64'}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(folder: Path, requirements: Path, python_version: str, target_platform: str):
    """记录 pip 下载成功后实际得到的 wheel 文件和目标环境."""
    manifest = {
        'format': 1, 'role': 'sender',
        'target': {'python': python_version, 'implementation': 'cpython', 'platform': target_platform},
        'requirements_sha256': sha256(requirements),
        'wheels': {path.name: sha256(path) for path in sorted(folder.glob('*.whl'))},
    }
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def verify_wheelhouse(folder: Path, requirements: Path, check_host: bool = False) -> dict:
    """在分发或安装边界校验清单、文件摘要和可选的 Linux 目标环境."""
    manifest_path = folder / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'无法读取离线包清单 {manifest_path}: {exc}') from exc
    if not isinstance(manifest, dict) or manifest.get('format') != 1 or manifest.get('role') != 'sender':
        raise ValueError(f'离线包清单格式错误: {manifest_path}')
    target = manifest.get('target')
    if not isinstance(target, dict) or target.get('implementation') != 'cpython' or target.get('platform') not in PLATFORMS or target.get('python') not in ('3.10', '3.11', '3.12'):
        raise ValueError('离线包目标必须为 CPython 3.10-3.12 和 manylinux2014 x86_64/aarch64.')
    if manifest.get('requirements_sha256') != sha256(requirements):
        raise ValueError('离线包与当前 requirements-tx.txt 不匹配, 请重新下载.')
    wheels = manifest.get('wheels')
    if not isinstance(wheels, dict) or not wheels or set(wheels) != {p.name for p in folder.glob('*.whl')}:
        raise ValueError('离线包 wheel 文件缺失或混入了未声明的文件.')
    for name, digest in wheels.items():
        if Path(name).name != name or not name.endswith('.whl') or sha256(folder / name) != digest:
            raise ValueError(f'离线依赖文件摘要错误: {name}')
    if check_host:
        current_python = f'{sys.version_info.major}.{sys.version_info.minor}'
        if sys.platform != 'linux' or sys.implementation.name != 'cpython' or current_python != target['python'] or platform.machine() != PLATFORMS[target['platform']]:
            raise ValueError(f'离线包要求 Linux CPython {target["python"]} {PLATFORMS[target["platform"]]}, 当前环境不匹配.')
        libc, version = platform.libc_ver()
        if libc != 'glibc' or tuple(int(part) for part in version.split('.')[:2]) < (2, 17):
            raise ValueError('此离线包要求 glibc >= 2.17, 不适用于 Alpine/musl Linux.')
    return manifest

"""校验四种发送/接收离线包的角色、Python、平台和文件摘要."""

import hashlib
import json
import platform
import sys
from pathlib import Path


ROLE_REQUIREMENTS = {'sender': 'requirements-tx.txt', 'receiver': 'requirements-rx.txt'}
PLATFORMS = {
    'win32': ('Windows', 'i686', None),
    'win_amd64': ('Windows', 'x86_64', None),
    'manylinux2014_x86_64': ('Linux', 'x86_64', (2, 17)),
    'manylinux2014_aarch64': ('Linux', 'aarch64', (2, 17)),
    'manylinux2014_i686': ('Linux', 'i686', (2, 17)),
    'manylinux_2_28_x86_64': ('Linux', 'x86_64', (2, 28)),
    'manylinux_2_28_aarch64': ('Linux', 'aarch64', (2, 28)),
}
TARGETS = ('windows-sender', 'windows-receiver', 'linux-sender', 'linux-receiver')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pip_platforms(target_platform: str) -> list[str]:
    """2.28 环境同时接受更早 manylinux 标签, 避免拒绝仍兼容的依赖."""
    if target_platform.startswith('manylinux_2_28_'):
        arch = PLATFORMS[target_platform][1]
        return [target_platform, 'manylinux2014_' + arch, 'manylinux_2_17_' + arch]
    return [target_platform]


def write_manifest(folder: Path, requirements: Path, role: str, python_version: str, target_platform: str):
    """仅在全部下载成功后记录清单; 不兼容旧的仅发送端清单."""
    manifest = {
        'format': 2, 'role': role,
        'target': {'python': python_version, 'implementation': 'cpython', 'platform': target_platform},
        'requirements_sha256': sha256(requirements),
        'wheels': {path.name: sha256(path) for path in sorted(folder.glob('*.whl'))},
    }
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def verify_wheelhouse(folder: Path, requirements: Path, role: str, system: str, check_host: bool = False) -> dict:
    """分发/安装边界拒绝角色、操作系统、摘要或宿主不匹配的离线库."""
    manifest_path = folder / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'无法读取离线包清单 {manifest_path}: {exc}') from exc
    if not isinstance(manifest, dict) or manifest.get('format') != 2 or manifest.get('role') != role:
        raise ValueError(f'离线包清单版本或收发角色错误: {manifest_path}')
    target = manifest.get('target')
    if not isinstance(target, dict) or target.get('implementation') != 'cpython' or target.get('platform') not in PLATFORMS or target.get('python') not in ('3.10', '3.11', '3.12'):
        raise ValueError('离线包目标必须为支持的平台和 CPython 3.10-3.12.')
    expected_system, machine, libc_min = PLATFORMS[target['platform']]
    if system != expected_system:
        raise ValueError(f'离线库要求 {expected_system}, 不能用于 {system} 包.')
    if manifest.get('requirements_sha256') != sha256(requirements):
        raise ValueError('离线库与当前角色依赖清单不匹配, 请重新下载.')
    wheels = manifest.get('wheels')
    if not isinstance(wheels, dict) or not wheels or set(wheels) != {p.name for p in folder.glob('*.whl')}:
        raise ValueError('离线包 wheel 文件缺失或混入了未声明的文件.')
    for name, digest in wheels.items():
        if Path(name).name != name or not name.endswith('.whl') or sha256(folder / name) != digest:
            raise ValueError(f'离线依赖文件摘要错误: {name}')
    if check_host:
        current_python = f'{sys.version_info.major}.{sys.version_info.minor}'
        current_machine = platform.machine().lower()
        current_machine = 'x86_64' if current_machine == 'amd64' else current_machine
        if current_machine in ('x86', 'i386', 'i686', 'x86_64') and sys.maxsize <= 2 ** 32:
            current_machine = 'i686'
        if platform.system() != system or sys.implementation.name != 'cpython' or current_python != target['python'] or current_machine != machine:
            raise ValueError(f'离线库要求 {system} CPython {target["python"]} {machine}, 当前环境不匹配.')
        if libc_min is not None:
            libc, version = platform.libc_ver()
            if libc != 'glibc' or tuple(int(part) for part in version.split('.')[:2]) < libc_min:
                raise ValueError(f'离线库要求 glibc >= {libc_min[0]}.{libc_min[1]}, 不适用于当前 libc.')
    return manifest

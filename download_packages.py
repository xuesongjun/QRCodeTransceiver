#!/usr/bin/env python3
"""
download_packages.py - 离线下载 Python 依赖包
用法：在有网络的 Windows 机器上运行此脚本，下载 Linux 离线包
"""

import subprocess
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).parent.resolve()
    packages_dir = script_dir / "offline_packages"
    requirements_file = script_dir / "requirements.txt"

    # 检查 requirements.txt 是否存在
    if not requirements_file.exists():
        print("错误：找不到 requirements.txt")
        sys.exit(1)

    # 创建离线包目录
    packages_dir.mkdir(exist_ok=True)

    print("=" * 50)
    print("离线下载 Python 依赖包")
    print("=" * 50)
    print(f"目标目录: {packages_dir}")
    print()

    # 询问目标平台
    print("请选择目标平台：")
    print("1) Linux x86_64 (manylinux)")
    print("2) Linux aarch64/arm64")
    print("3) Windows (当前平台)")

    choice = input("请输入选项 [1]: ").strip() or "1"

    # Python 版本选择
    print()
    print("请选择目标 Python 版本：")
    print("1) Python 3.8 (推荐，兼容性最好)")
    print("2) Python 3.9")
    print("3) Python 3.10")
    print("4) Python 3.11")
    print("5) Python 3.12")

    py_choice = input("请输入选项 [1]: ").strip() or "1"
    py_versions = {"1": "3.8", "2": "3.9", "3": "3.10", "4": "3.11", "5": "3.12"}
    python_version = py_versions.get(py_choice, "3.8")

    print()
    print(f"目标 Python 版本: {python_version}")
    print()

    def download_for_platform(platform: str, python_ver: str):
        """下载指定平台的包"""
        print(f"下载 {platform} 平台的包...")

        cmd = [
            sys.executable, "-m", "pip", "download",
            "-r", str(requirements_file),
            "-d", str(packages_dir),
            "--platform", platform,
            "--python-version", python_ver,
            "--only-binary=:all:"
        ]

        try:
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError:
            print(f"警告：部分包下载失败 ({platform})")
            return False

    def download_current_platform():
        """下载当前平台的包"""
        print("下载当前平台的包...")
        cmd = [
            sys.executable, "-m", "pip", "download",
            "-r", str(requirements_file),
            "-d", str(packages_dir)
        ]
        subprocess.run(cmd, check=True)

    # 根据选择下载
    if choice == "1":
        download_for_platform("manylinux2014_x86_64", python_version)
    elif choice == "2":
        download_for_platform("manylinux2014_aarch64", python_version)
    elif choice == "3":
        download_current_platform()
    else:
        print("无效选项")
        sys.exit(1)

    # 统计下载的包
    packages = list(packages_dir.glob("*.whl")) + list(packages_dir.glob("*.tar.gz"))

    print()
    print("=" * 50)
    print("下载完成！")
    print("=" * 50)
    print()
    print(f"共下载 {len(packages)} 个包")
    print(f"离线包目录: {packages_dir}")
    print()
    print("将以下文件/目录复制到目标 Linux 机器：")
    print(f"  1. {packages_dir.name}/ 目录")
    print("  2. install_offline.sh 脚本")
    print("  3. requirements.txt")
    print("  4. qrcode_transceiver 项目文件 (*.py, FF.py 等)")
    print()
    print("在目标机器上运行:")
    print("  chmod +x install_offline.sh")
    print("  ./install_offline.sh")
    print()

    # 列出下载的包
    print("已下载的包：")
    for pkg in sorted(packages):
        size_kb = pkg.stat().st_size / 1024
        if size_kb > 1024:
            size_str = f"{size_kb/1024:.1f} MB"
        else:
            size_str = f"{size_kb:.1f} KB"
        print(f"  {pkg.name} ({size_str})")


if __name__ == "__main__":
    main()

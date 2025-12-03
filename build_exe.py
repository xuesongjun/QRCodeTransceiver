#!/usr/bin/env python3
"""
build_exe.py - 打包 qrcode_rx_gui.py 为 exe
"""

import subprocess
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).parent.resolve()
    main_script = script_dir / "qrcode_rx_gui.py"

    if not main_script.exists():
        print(f"错误：找不到 {main_script}")
        sys.exit(1)

    # 检查 pyinstaller
    try:
        import PyInstaller
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    print("开始打包...")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "QRReceiver",
        "--add-data", f"{script_dir / 'fountain.py'};.",
        str(main_script)
    ]

    subprocess.run(cmd, check=True, cwd=str(script_dir))

    exe_path = script_dir / "dist" / "QRReceiver.exe"
    if exe_path.exists():
        print()
        print("=" * 50)
        print("打包完成！")
        print("=" * 50)
        print(f"exe 文件：{exe_path}")
    else:
        print("打包可能失败，请检查输出")


if __name__ == "__main__":
    main()

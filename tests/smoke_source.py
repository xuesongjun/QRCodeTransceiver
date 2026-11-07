"""实际解压离线源码包, 创建无系统库的 venv, 验证脚本可运行."""

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_video_rx import VideoIntegrationTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--target', required=True, choices=('windows-sender', 'windows-receiver', 'linux-sender', 'linux-receiver'))
    args = parser.parse_args()
    system, role = args.target.split('-')
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='source-check-', dir=ROOT / 'build') as temp:
        folder = Path(temp)
        with zipfile.ZipFile(args.package) as package:
            package.extractall(folder)
        source = folder / ('QRCodeSender' if role == 'sender' else 'QRCodeReceiver')
        temporary = folder / 'temporary'
        temporary.mkdir()
        env = {**os.environ, 'TMP': str(temporary), 'TEMP': str(temporary), 'TMPDIR': str(temporary),
               'PIP_CACHE_DIR': str(folder / 'pip-cache'), 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
        subprocess.run([sys.executable, str(source / 'install_package.py'), '--target', args.target, '--offline'], env=env, check=True, timeout=180)
        python = source / ('venv/Scripts/python.exe' if system == 'windows' else 'venv/bin/python')
        gui = 'qrcode_tx_gui.py' if role == 'sender' else 'qrcode_rx_gui.py'
        subprocess.run([str(python), str(source / gui), '--self-test'], env=env, check=True, timeout=60)
        if role == 'receiver':
            VideoIntegrationTests.setUpClass()
            try:
                output = folder / 'received'
                subprocess.run([str(python), str(source / 'qrcode_rx.py'), '--video', str(VideoIntegrationTests.video), '--output-dir', str(output), '--quiet'], env=env, check=True, timeout=90)
                if (output / 'restored.bin').read_bytes() != VideoIntegrationTests.body:
                    raise RuntimeError('离线脚本恢复内容不一致')
            finally:
                VideoIntegrationTests.tearDownClass()
    print(f'PASS: {args.target} 离线源码安装和脚本运行')


if __name__ == '__main__':
    main()

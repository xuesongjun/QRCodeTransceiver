"""在新目录解压实际 Windows ZIP, 验证 GUI 启动和 exe 的 MP4 文件还原."""

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
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='frozen-check-', dir=ROOT / 'build') as temp:
        folder = Path(temp)
        with zipfile.ZipFile(args.package) as package:
            package.extractall(folder)
        receiver = folder / 'QRCodeReceiver'
        cli = receiver / 'QRReceiveCLI.exe'
        gui = receiver / 'QRReceiver.exe'
        # 只保留系统 PATH, 不让开发机的 Python 环境参与成品验收.
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
        system_root = Path(os.environ['SystemRoot'])
        environment['PATH'] = str(system_root / 'System32') + os.pathsep + str(system_root)
        def run(arguments):
            return subprocess.run(arguments, cwd=folder, env=environment, capture_output=True, timeout=90)
        help_result = run([str(cli), '--help'])
        if help_result.returncode != 0 or b'--video' not in help_result.stdout:
            raise RuntimeError(f'CLI 启动失败: {help_result.stderr!r}')
        gui_result = run([str(gui), '--self-test'])
        if gui_result.returncode != 0:
            raise RuntimeError(f'GUI 启动失败: {gui_result.stderr!r}')
        VideoIntegrationTests.setUpClass()
        try:
            restored = folder / 'restored'
            result = run([str(cli), '--video', str(VideoIntegrationTests.video), '--output-dir', str(restored), '--quiet'])
            if result.returncode != 0:
                raise RuntimeError(f'MP4 恢复失败: {result.stderr!r}')
            if (restored / 'restored.bin').read_bytes() != VideoIntegrationTests.body:
                raise RuntimeError('恢复文件与原文件不同')
            incomplete = folder / 'incomplete'
            result = run([str(cli), '--video', str(VideoIntegrationTests.incomplete), '--output-dir', str(incomplete), '--quiet'])
            if result.returncode != 2 or list(incomplete.iterdir()):
                raise RuntimeError(f'数据不足处理错误: {result.returncode}, {result.stderr!r}')
            result = run([str(cli), '--video', str(folder / 'missing.mp4'), '--output-dir', str(restored), '--quiet'])
            if result.returncode != 1:
                raise RuntimeError('缺失视频未返回失败状态')
        finally:
            VideoIntegrationTests.tearDownClass()
    print('PASS: 解压成品后 GUI/CLI 启动、移动 MP4 字节一致恢复、数据不足和缺失文件状态.')


if __name__ == '__main__':
    main()

"""解压实际 Windows/Linux 便携 ZIP, 隔离 Python 路径后验证双端 GUI/CLI."""

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_video_rx import VideoIntegrationTests
from qr_decode import decode_with_fallback
from transfer_state import ReceiveTask, SendTask
from transfer_protocol import Packet, SOURCE
from video_samples import write_video, stalled_sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--role', required=True, choices=('sender', 'receiver'))
    args = parser.parse_args()
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='frozen-check-', dir=ROOT / 'build') as temp:
        folder = Path(temp)
        with zipfile.ZipFile(args.package) as package:
            package.extractall(folder)
        top = folder / ('QRCodeSender' if args.role == 'sender' else 'QRCodeReceiver')
        suffix = '.exe' if sys.platform == 'win32' else ''
        names = ('QRSend', 'QRSendCLI') if args.role == 'sender' else ('QRReceiver', 'QRReceiveCLI')
        gui, cli = (top / (name + suffix) for name in names)
        if sys.platform != 'win32':
            gui.chmod(0o755)
            cli.chmod(0o755)
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
        if sys.platform == 'win32':
            system = Path(os.environ['SystemRoot'])
            environment['PATH'] = str(system / 'System32') + os.pathsep + str(system)
        else:
            environment['PATH'] = '/usr/bin:/bin'
            environment.pop('LD_LIBRARY_PATH', None)
        def run(arguments):
            return subprocess.run(arguments, cwd=folder, env=environment, capture_output=True, timeout=90)
        result = run([str(gui), '--self-test'])
        if result.returncode != 0:
            raise RuntimeError(f'GUI 启动失败: {result.stderr!r}')
        result = run([str(cli), '--help'])
        if result.returncode != 0:
            raise RuntimeError(f'CLI 启动失败: {result.stderr!r}')
        if args.role == 'sender':
            original = b'Test native sender GUI and CLI package'
            source = folder / 'input.bin'
            source.write_bytes(original)
            output = folder / 'qr'
            frozen = folder / 'frozen.qrtx'
            result = run([str(cli), str(source), '--task', str(frozen), '--no-display', '--no-compress', '--chunk-size', '128', '--extra', '0', '--output', str(output)])
            if result.returncode != 0:
                raise RuntimeError(f'二维码生成失败: {result.stderr!r}')
            receiver = None
            for path in sorted(output.glob('*.png')):
                for text, _ in decode_with_fallback(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE), True):
                    packet = Packet.read(text)
                    if receiver is None:
                        receiver = ReceiveTask(packet.parameters)
                    receiver.feed(text)
            receiver.finish()
            target = receiver.write_output(folder / 'result')
            if target.read_bytes() != original:
                raise RuntimeError('成品生成的二维码与输入文件不同')
            sender = SendTask.load(frozen)
            partial = ReceiveTask(sender.parameters)
            partial.feed(Packet(sender.parameters, SOURCE, 0, 1, sender.source(0)).text())
            request = folder / 'repair.qrreq'
            request.write_text(partial.feedback().code, encoding='ascii')
            source.write_bytes(b'original changed after first send')
            repair_output = folder / 'repair-images'
            result = run([str(cli), '--task', str(frozen), '--repair', str(request), '--no-display', '--output', str(repair_output)])
            if result.returncode != 0:
                raise RuntimeError(f'发送成品补传失败: {result.stderr!r}')
            indices = []
            for path in sorted(repair_output.glob('*.png')):
                for text, _ in decode_with_fallback(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE), True):
                    packet = Packet.read(text)
                    indices.append(packet.number)
                    partial.feed(text)
            if indices != list(range(1, sender.parameters.num_chunks)):
                raise RuntimeError('发送成品没有精确按请求补传')
            partial.finish()
            if not partial.verified:
                raise RuntimeError('发送成品冻结任务补传没有恢复原数据')
        else:
            VideoIntegrationTests.setUpClass()
            try:
                restored = folder / 'restored'
                result = run([str(cli), '--video', str(VideoIntegrationTests.video), '--output-dir', str(restored), '--quiet'])
                if result.returncode != 0 or (restored / 'restored.bin').read_bytes() != VideoIntegrationTests.body:
                    raise RuntimeError(f'MP4 还原失败: {result.stderr!r}')
                incomplete = folder / 'incomplete'
                result = run([str(cli), '--video', str(VideoIntegrationTests.incomplete), '--output-dir', str(incomplete), '--quiet'])
                if result.returncode != 2 or list(incomplete.glob('*.bin')) or not list((incomplete / 'tasks').glob('*.qrrx')):
                    raise RuntimeError('数据不足时没有正确报告失败')
                state = next((incomplete / 'tasks').glob('*.qrrx'))
                request = folder / 'request.qrreq'
                result = run([str(cli), '--task', str(state), '--request-out', str(request)])
                if result.returncode != 2:
                    raise RuntimeError(f'成品重开现场/导出请求失败: {result.stderr!r}')
                receiver = ReceiveTask.load(state)
                sender = VideoIntegrationTests.sender
                requested = sender.request(request.read_text().strip()).requested
                repair_video = folder / 'repair.mp4'
                write_video(repair_video, [Packet(sender.parameters, SOURCE, i, 8, sender.source(i)).text() for i in reversed(requested)])
                result = run([str(cli), '--task', str(state), '--video', str(repair_video), '--output-dir', str(incomplete)])
                if result.returncode != 0 or (incomplete / 'restored.bin').read_bytes() != VideoIntegrationTests.body:
                    raise RuntimeError(f'接收成品续收补传失败: {result.stderr!r}')
                result = run([str(cli), '--video', str(folder / 'missing.mp4'), '--output-dir', str(restored), '--quiet'])
                if result.returncode != 1:
                    raise RuntimeError('缺失视频没有正确报告失败')
                sender, body, packets, video = stalled_sample(folder)
                output = folder / 'gaussian-output'
                result = run([str(cli), '--video', str(video), '--output-dir', str(output)])
                if result.returncode != 0 or (output / 'gaussian.bin').read_bytes() != body:
                    raise RuntimeError(f'接收成品未完成满秩高斯译码: {result.stderr!r}')
            finally:
                VideoIntegrationTests.tearDownClass()
    print(f'PASS: {args.role} 成品 GUI/CLI 启动及数据内容验收')


if __name__ == '__main__':
    main()

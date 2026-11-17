"""后台发送使用冻结任务, 只生成补传所需源块, 取消可终止有界队列."""

import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from qrcode_tx_gui import SenderApp
from transfer_protocol import Packet
from transfer_state import SendTask


class SenderWorkerTests(unittest.TestCase):
    def test_background_sender_targets_exact_sources_and_stops(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            source = Path(temp) / 'hello.txt'
            source.write_bytes(b'Hello from sender UI')
            task = SendTask.create(source, 128)
            task.save(Path(temp) / 'send.qrtx')
            frames, events, stop = queue.Queue(maxsize=2), queue.Queue(), threading.Event()
            with patch('qrcode_tx_gui.build_qr', side_effect=lambda text, *args: text):
                worker = threading.Thread(target=SenderApp._produce, args=(task, (1,), 400, frames, events, stop))
                worker.start()
                text, round_no, offset, total = frames.get(timeout=10)
                stop.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(events.empty())
            packet = Packet.read(text)
            self.assertEqual((packet.number, packet.data, offset, total), (1, task.source(1), 1, 1))

    def test_render_failure_is_reported(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            path = Path(temp) / 'input.bin'
            path.write_bytes(b'x')
            frames, events = queue.Queue(maxsize=2), queue.Queue()
            with patch('qrcode_tx_gui.build_qr', side_effect=ValueError('QR too dense')):
                SenderApp._produce(SendTask.create(path), None, 400, frames, events, threading.Event())
            self.assertEqual(events.get_nowait()[0], 'error')
            self.assertTrue(frames.empty())


if __name__ == '__main__':
    unittest.main()

"""视频接收回归: 移动、重新定位、重复帧、MP4 恢复及失败提示."""

import argparse
import contextlib
import io
import queue
import random
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import qrcode_rx as rx
import qrcode_rx_gui as gui
from fountain import Fountain
from qrcode_tx import build_payload, build_qr
from qrcode_video import VideoScanner


class TrackingTests(unittest.TestCase):
    def test_tracking_translates_coordinates_and_reacquires_same_frame(self):
        calls = []
        detections = iter([
            [("packet", (100, 100, 80, 80))],
            [("packet", (40, 45, 80, 80))],
            [],
            [("packet", (500, 300, 80, 80))],
        ])

        def decode(image, multi):
            calls.append((image.shape, multi))
            return next(detections)

        scanner = VideoScanner(Path("unused.mp4"), decode, lambda text: text == "packet")
        frame = np.zeros((480, 640), dtype=np.uint8)
        self.assertEqual(scanner._scan(frame), ["packet"])
        first = scanner.region
        self.assertEqual(scanner._scan(frame), ["packet"])
        self.assertEqual(scanner.region[:2], (first[0] + 8, first[1] + 13))
        self.assertEqual(scanner._scan(frame), ["packet"])
        self.assertEqual([multi for _, multi in calls], [True, False, False, True])
        self.assertEqual(calls[-1][0], frame.shape)
        left, top, width, height = scanner.region
        self.assertLessEqual(left + width, 640)
        self.assertLessEqual(top + height, 480)

    def test_unrelated_qr_does_not_lock_region(self):
        scanner = VideoScanner(
            Path("unused.mp4"),
            lambda image, multi: [("https://example.com", (10, 10, 80, 80))],
            rx.validate_droplet,
        )
        self.assertEqual(scanner._scan(np.zeros((240, 320), dtype=np.uint8)), [])
        self.assertIsNone(scanner.region)

    def test_identical_frames_are_not_decoded_again(self):
        frame = np.full((240, 320, 3), 255, dtype=np.uint8)
        with patch("qrcode_video.cv2.VideoCapture") as factory, patch.object(Path, "is_file", return_value=True):
            capture = factory.return_value
            capture.isOpened.return_value = True
            capture.get.return_value = 2
            capture.read.side_effect = [(True, frame), (True, frame.copy()), (False, None)]
            calls = []
            with VideoScanner(Path("test.mp4"), lambda image, multi: calls.append(multi) or [], rx.validate_droplet) as scanner:
                self.assertEqual(list(scanner), [[], []])
                self.assertEqual(scanner.frame_index, 2)
            self.assertEqual(calls, [True])
            capture.release.assert_called_once()

    def test_video_without_readable_frames_is_an_error(self):
        with patch("qrcode_video.cv2.VideoCapture") as factory, patch.object(Path, "is_file", return_value=True):
            capture = factory.return_value
            capture.isOpened.return_value = True
            capture.get.return_value = 0
            capture.read.return_value = (False, None)
            with self.assertRaisesRegex(ValueError, "没有可读取的画面"):
                with VideoScanner(Path("empty.mp4"), rx.decode_with_fallback, rx.validate_droplet) as scanner:
                    list(scanner)
            capture.release.assert_called_once()

    def test_video_rejects_screen_only_region_options(self):
        with patch("sys.argv", ["qrcode_rx.py", "--video", "test.mp4", "--no-auto-region"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                rx.parse_args()
        self.assertEqual(error.exception.code, 2)


class VideoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="qr-video-test-", dir=Path(__file__).resolve().parents[1])
        cls.root = Path(cls.temp.name)
        cls.body = random.Random(42).randbytes(700)
        fountain = Fountain(build_payload("restored.bin", cls.body), chunk_size=128)
        cls.droplets = [fountain.droplet().getStr() for _ in range(36)]
        cls.video = cls.root / "移动二维码.mp4"
        cls._write_video(cls.video, cls.droplets, moving=True)
        cls.incomplete = cls.root / "incomplete.mp4"
        cls._write_video(cls.incomplete, cls.droplets[:1], moving=False)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def _write_video(cls, path, droplets, moving):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12, (960, 720))
        if not writer.isOpened():
            raise RuntimeError("测试环境无法生成 MP4 视频")
        try:
            writer.write(np.full((720, 960, 3), 255, dtype=np.uint8))
            for index, droplet in enumerate(droplets):
                qr = np.asarray(build_qr(droplet, size=360))
                if moving:
                    angle = (-4, 0, 4)[index % 3]
                    qr = cv2.warpAffine(
                        qr, cv2.getRotationMatrix2D((180, 180), angle, 1), (360, 360),
                        borderValue=(255, 255, 255),
                    )
                    # 包含连续漂移和跨越原识别区域的大幅跳动.
                    left = (30 + index * 13) % 180 if index % 6 < 3 else 550 - index % 3 * 13
                    top = 30 + index % 4 * 65
                else:
                    left, top = 30, 30
                frame = np.full((720, 960, 3), 255, dtype=np.uint8)
                frame[top:top + 360, left:left + 360] = cv2.cvtColor(qr, cv2.COLOR_RGB2BGR)
                writer.write(frame)
                writer.write(frame)
                if moving and index % 7 == 3:
                    writer.write(np.full_like(frame, 255))
        finally:
            writer.release()

    def args(self, path, **kwargs):
        return argparse.Namespace(video=path, quiet=True, debug=False, no_save=False, **kwargs)

    def test_cli_recovers_moving_mp4_without_screen_capture(self):
        output = self.root / "cli"
        with patch("sys.argv", ["qrcode_rx.py", "--video", str(self.video), "--output-dir", str(output), "--quiet"]), patch("qrcode_rx.mss", side_effect=AssertionError("视频模式不应截屏")):
            self.assertEqual(rx.main(), 0)
        self.assertEqual((output / "restored.bin").read_bytes(), self.body)

    def test_gui_worker_recovers_same_mp4(self):
        decoder = gui.Decoder(self.root / "gui")
        events = queue.Queue()
        app = gui.ReceiverApp.__new__(gui.ReceiverApp)
        app._video_worker_loop(self.video, decoder, threading.Event(), events)
        messages = []
        while not events.empty():
            messages.append(events.get_nowait())
        self.assertEqual((decoder.output_dir / "restored.bin").read_bytes(), self.body)
        self.assertEqual(sum(event == "saved" for event, _ in messages), 1)
        self.assertIn("解析完成", messages[-1][1])

    def test_incomplete_video_reports_failure(self):
        saver = rx.DropletAutoSaver(self.root / "incomplete-output", verbose=False)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(rx.receive_video(self.args(self.incomplete), saver), 2)
        self.assertIn("传输未完成", stderr.getvalue())
        self.assertEqual(list(saver.output_dir.iterdir()), [])

    def test_raw_extraction_deduplicates_repeated_video_frames(self):
        args = self.args(self.incomplete)
        args.no_save = True
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(rx.receive_video(args, None), 0)
        self.assertEqual(stdout.getvalue().splitlines(), self.droplets[:1])

    def test_invalid_video_reports_error(self):
        invalid = self.root / "not-video.mp4"
        invalid.write_bytes(b"not a video")
        with self.assertRaisesRegex(ValueError, "无法打开视频"):
            with VideoScanner(invalid, rx.decode_with_fallback, rx.validate_droplet):
                pass

    def test_missing_video_reports_error(self):
        with self.assertRaises(FileNotFoundError):
            with VideoScanner(self.root / "missing.mp4", rx.decode_with_fallback, rx.validate_droplet):
                pass

    def test_cancellation_does_not_restore_a_file(self):
        decoder = gui.Decoder(self.root / "cancelled")
        stop = threading.Event()
        stop.set()
        events = queue.Queue()
        gui.ReceiverApp.__new__(gui.ReceiverApp)._video_worker_loop(self.video, decoder, stop, events)
        self.assertEqual(list(decoder.output_dir.iterdir()), [])
        self.assertTrue(events.empty())


if __name__ == "__main__":
    unittest.main()

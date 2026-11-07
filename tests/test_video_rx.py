"""真实 MP4 的顺序解析、续收补传、移动跟随和录制解耦回归."""

import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import qrcode_rx as rx
from fountain import Fountain
from legacy_receive import receive_legacy_video
from qr_decode import decode_with_fallback
from qrcode_video import MovingQRScanner, VideoScanner
from receive_pipeline import process_video
from screen_record import record_screen
from transfer_protocol import Packet, SOURCE, is_transfer_text
from transfer_state import ReceiveTask
from video_samples import samples, write_video

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

        scanner = MovingQRScanner(decode, lambda text: text == "packet")
        frame = np.zeros((480, 640), dtype=np.uint8)
        self.assertEqual(scanner.scan(frame), ["packet"])
        first = scanner.region
        self.assertEqual(scanner.scan(frame), ["packet"])
        self.assertEqual(scanner.region[:2], (first[0] + 8, first[1] + 13))
        self.assertEqual(scanner.scan(frame), ["packet"])
        self.assertEqual([multi for _, multi in calls], [True, False, False, True])
        self.assertEqual(calls[-1][0], frame.shape)
        left, top, width, height = scanner.region
        self.assertLessEqual(left + width, 640)
        self.assertLessEqual(top + height, 480)

    def test_unrelated_qr_does_not_lock_region(self):
        scanner = MovingQRScanner(
            lambda image, multi: [("https://example.com", (10, 10, 80, 80))],
            is_transfer_text,
        )
        self.assertEqual(scanner.scan(np.zeros((240, 320), dtype=np.uint8)), [])
        self.assertIsNone(scanner.region)

    def test_identical_frames_are_not_decoded_again(self):
        frame = np.full((240, 320, 3), 255, dtype=np.uint8)
        with patch("qrcode_video.cv2.VideoCapture") as factory, patch.object(Path, "is_file", return_value=True):
            capture = factory.return_value
            capture.isOpened.return_value = True
            capture.get.return_value = 2
            capture.read.side_effect = [(True, frame), (True, frame.copy()), (False, None)]
            calls = []
            with VideoScanner(Path("test.mp4"), lambda image, multi: calls.append(multi) or [], is_transfer_text) as scanner:
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
                with VideoScanner(Path("empty.mp4"), decode_with_fallback, is_transfer_text) as scanner:
                    list(scanner)
            capture.release.assert_called_once()

class VideoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='qr-video-test-', dir=Path(__file__).resolve().parents[1] / 'build')
        cls.root = Path(cls.temp.name)
        cls.sender, cls.body, cls.video, cls.incomplete = samples(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cli_recovers_moving_mp4_without_screen_capture(self):
        output = self.root / 'cli'
        with patch('screen_record.mss', side_effect=AssertionError('导入视频不能截屏')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(rx.main(['--video', str(self.video), '--output-dir', str(output), '--quiet']), 0)
        self.assertEqual((output / 'restored.bin').read_bytes(), self.body)

    def test_save_restart_feedback_targeted_video_repeat_and_final_hash(self):
        output, state = self.root / 'workflow', self.root / 'workflow.qrrx'
        first = process_video(self.incomplete, None, output, threading.Event(), state)
        self.assertTrue(first.state_saved)
        self.assertFalse(first.task.verified)
        done = first.task.done
        receiver = ReceiveTask.load(state)
        duplicate = process_video(self.incomplete, receiver, output, threading.Event())
        self.assertTrue(duplicate.duplicate_video)
        self.assertEqual((receiver.done, duplicate.new_blocks), (done, 0))
        feedback = receiver.feedback()
        requested = self.sender.request(feedback.code).requested
        repair = self.root / 'repair.mp4'
        write_video(repair, [Packet(self.sender.parameters, SOURCE, i, 5, self.sender.source(i)).text() for i in reversed(requested)])
        final = process_video(repair, receiver, output, threading.Event())
        self.assertEqual(final.error, '')
        self.assertTrue(final.task.verified)
        self.assertTrue(final.state_saved)
        self.assertEqual((output / 'restored.bin').read_bytes(), self.body)
        repeated = process_video(repair, ReceiveTask.load(state), output, threading.Event())
        self.assertEqual(repeated.new_blocks, 0)
        self.assertTrue(repeated.task.verified)

    def test_recording_is_independent_and_saved_video_uses_same_parser(self):
        stop = threading.Event()
        capture = cv2.VideoCapture(str(self.video))
        last = np.full((640, 960, 3), 255, dtype=np.uint8)
        def grab(region):
            nonlocal last
            ok, frame = capture.read()
            if ok:
                last = frame
            else:
                stop.set()
            return cv2.cvtColor(last, cv2.COLOR_BGR2BGRA)
        path = self.root / 'recording.mp4'
        try:
            with patch('screen_record.mss') as factory, patch('qr_decode.decode_with_fallback', side_effect=AssertionError('录制不能识别')):
                camera = factory.return_value.__enter__.return_value
                camera.monitors = [{}, {'left': 0, 'top': 0, 'width': 960, 'height': 640}]
                camera.grab.side_effect = grab
                count, elapsed = record_screen(path, stop, fps=120)
            self.assertGreater(count, 0)
        finally:
            capture.release()
        result = process_video(path, None, self.root / 'recorded-output', threading.Event())
        self.assertEqual(result.error, '')
        self.assertTrue(result.task.verified)
        self.assertEqual(Path(result.task.output_path).read_bytes(), self.body)

    def test_legacy_video_is_separate_one_time_entry(self):
        body = b'old-video-content'
        fountain = Fountain(b'old.bin|1|1\n' + body, 128)
        path = self.root / 'legacy.mp4'
        write_video(path, [fountain.droplet().getStr()])
        output = self.root / 'legacy-output'
        files = receive_legacy_video(path, output, threading.Event())
        self.assertEqual(len(files), 1)
        self.assertEqual((output / 'old.bin').read_bytes(), body)
        self.assertIsNone(process_video(path, None, self.root / 'new-cannot-merge', threading.Event()).task)

    def test_failed_save_is_reported_and_progress_retained(self):
        with patch('transfer_state.os.replace', side_effect=OSError('disk full')):
            result = process_video(self.incomplete, None, self.root / 'save-failed', threading.Event())
        self.assertFalse(result.state_saved)
        self.assertGreater(result.task.done, 0)
        self.assertIn('保存失败', result.error)

    def test_invalid_and_missing_video_report_error(self):
        for path in (self.root / 'missing.mp4',):
            result = process_video(path, None, self.root / 'invalid', threading.Event())
            self.assertIsNone(result.task)
            self.assertTrue(result.error)

    def test_cancellation_saves_existing_task(self):
        result = process_video(self.incomplete, None, self.root / 'cancel', threading.Event())
        stop = threading.Event()
        stop.set()
        cancelled = process_video(self.video, result.task, self.root / 'cancel', stop)
        self.assertTrue(cancelled.cancelled)
        self.assertTrue(cancelled.state_saved)
        self.assertEqual(cancelled.new_blocks, 0)


if __name__ == '__main__':
    unittest.main()

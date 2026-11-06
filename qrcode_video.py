"""读取视频文件, 跟随移动的二维码并逐帧提取喷泉编码包."""

import zlib
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

import cv2
import numpy as np


Rect = Tuple[int, int, int, int]
Detection = Tuple[str, Optional[Rect]]


class VideoScanner:
    """
    顺序读取视频, 优先识别上一帧二维码附近, 失败时立即搜索当前完整帧.

    参数
    ----
    path : 视频文件路径.
    decode : 现有 QR 解码函数, 接收灰度图和 multi 参数.
    validate : 现有喷泉包校验函数, 用于排除画面中的无关二维码.

    使用 with 管理视频句柄. 每次迭代返回一帧中的有效编码包列表,
    不按视频播放速度等待, 不丢弃尚未处理的帧.
    """

    def __init__(
        self,
        path: Path,
        decode: Callable[[np.ndarray, bool], List[Detection]],
        validate: Callable[[str], bool],
    ):
        self.path = path
        self.decode = decode
        self.validate = validate
        self.frame_index = 0
        self.total_frames = 0
        self.region: Optional[Rect] = None
        self._last_signature = None

    def __enter__(self):
        if not self.path.is_file():
            raise FileNotFoundError(f"找不到视频文件: {self.path}")
        self.capture = cv2.VideoCapture(str(self.path))
        if not self.capture.isOpened():
            self.capture.release()
            raise ValueError(f"无法打开视频, 请检查文件及视频编码格式: {self.path}")
        # 帧总数仅用于显示进度, 不用于决定读取终点.
        self.total_frames = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.capture.release()

    def _detect(self, gray: np.ndarray, left: int, top: int, multi: bool) -> List[Detection]:
        detections = []
        for text, rect in self.decode(gray, multi):
            text = text.strip()
            if not self.validate(text):
                continue
            if rect is not None:
                x, y, width, height = rect
                rect = (left + x, top + y, width, height)
            detections.append((text, rect))
        return detections

    def _scan(self, gray: np.ndarray) -> List[str]:
        detections = []
        if self.region is not None:
            left, top, width, height = self.region
            crop = gray[top:top + height, left:left + width]
            detections = self._detect(crop, left, top, False)
        if not detections:
            # 移动、遮挡或场景切换时, 同一帧立即重新搜索, 保留喷泉译码状态.
            detections = self._detect(gray, 0, 0, True)
        self.region = None
        for _, rect in detections:
            if rect is None:
                continue
            left, top, width, height = rect
            padding = max(32, max(width, height) // 4)
            right = min(gray.shape[1], left + width + padding)
            bottom = min(gray.shape[0], top + height + padding)
            left = max(0, left - padding)
            top = max(0, top - padding)
            self.region = (left, top, right - left, bottom - top)
            break
        return [text for text, _ in detections]

    def __iter__(self) -> Iterator[List[str]]:
        while True:
            ok, frame = self.capture.read()
            if not ok:
                if self.frame_index == 0:
                    raise ValueError(f"视频中没有可读取的画面: {self.path}")
                return
            self.frame_index += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            signature = (gray.shape, zlib.crc32(gray.tobytes()))
            if signature == self._last_signature:
                yield []
                continue
            self._last_signature = signature
            yield self._scan(gray)

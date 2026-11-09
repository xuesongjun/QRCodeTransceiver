"""只采集和保存屏幕视频, 不在录制循环中执行二维码识别."""

import time
from pathlib import Path

import cv2
import numpy as np
from mss import MSS as mss


def record_screen(path: Path, stop_event, fps=30, monitor=1, seconds=None, progress=None, overwrite=False):
    if not 1 <= fps <= 120 or seconds is not None and seconds <= 0:
        raise ValueError('录制帧率应为 1-120, 录制时长应为正数')
    if path.exists() and not overwrite:
        raise FileExistsError(f'录制文件已存在: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    count, start = 0, time.monotonic()
    with mss() as capture:
        if not 1 <= monitor < len(capture.monitors):
            raise ValueError('屏幕编号无效')
        region = capture.monitors[monitor]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (region['width'], region['height']))
        if not writer.isOpened():
            writer.release()
            raise ValueError(f'无法创建录制视频: {path}')
        try:
            while not stop_event.is_set() and (seconds is None or time.monotonic() - start < seconds):
                frame_start = time.monotonic()
                frame = cv2.cvtColor(np.asarray(capture.grab(region)), cv2.COLOR_BGRA2BGR)
                writer.write(frame)
                count += 1
                if progress is not None:
                    progress(count, time.monotonic() - start)
                # 不追赶错过的时刻, 不伪造补帧; 记录实际采集率供用户判断.
                stop_event.wait(max(0, 1 / fps - (time.monotonic() - frame_start)))
        finally:
            writer.release()
    if count == 0:
        raise ValueError('没有录到画面, 未开始解析')
    verification = cv2.VideoCapture(str(path))
    try:
        ok, _ = verification.read()
        if not ok:
            raise ValueError('录制视频未能保存为可读取画面, 未开始解析')
    finally:
        verification.release()
    return count, time.monotonic() - start

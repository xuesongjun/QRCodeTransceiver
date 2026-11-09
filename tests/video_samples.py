"""生成真实移动二维码 MP4 样本, 供核心测试及解压后的交付包验收共用."""

import random
from pathlib import Path

import cv2
import numpy as np

from qr_render import build_qr
from fountain import robust_solition, randChunkNums, xor
from transfer_protocol import Packet, SOURCE, FOUNTAIN
from transfer_state import SendTask


def write_video(path, texts, moving=True):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 12, (960, 640))
    if not writer.isOpened():
        raise RuntimeError('测试环境不能写 MP4')
    try:
        for index, text in enumerate(texts):
            frame = np.full((640, 960, 3), 255, dtype=np.uint8)
            qr = cv2.cvtColor(np.array(build_qr(text, 400)), cv2.COLOR_RGB2BGR)
            x = (30 if index % 2 == 0 else 520) if moving else 200
            y = 40 + index % 3 * 70 if moving else 100
            frame[y:y + 400, x:x + 400] = qr
            writer.write(frame)
            writer.write(frame)
            if index % 3 == 1:
                writer.write(np.full_like(frame, 255))
    finally:
        writer.release()


def samples(root: Path):
    body = random.Random(42).randbytes(700)
    source = root / 'restored.bin'
    source.write_bytes(body)
    task = SendTask.create(source, 128, False)
    texts = [Packet(task.parameters, SOURCE, i, 1, task.source(i)).text() for i in range(task.parameters.num_chunks)]
    full, partial = root / '移动二维码.mp4', root / 'incomplete.mp4'
    write_video(full, texts)
    write_video(partial, texts[::2])
    return task, body, full, partial


def stalled_sample(root: Path):
    """三个真实 LT 包构成满秩无单元行系统, 旧剥皮算法无法起步."""
    original = random.Random(18).randbytes(160)
    path = root / 'gaussian.bin'
    path.write_bytes(original)
    sender = SendTask.create(path, 128, False)
    assert sender.parameters.num_chunks == 3
    packets = []
    for seed in (2, 10, 12):
        data = bytes(128)
        for index in randChunkNums(3, robust_solition(3), seed):
            data = xor(data, sender.source(index))
        packets.append(Packet(sender.parameters, FOUNTAIN, seed, 1, data))
    video = root / 'gaussian.mp4'
    write_video(video, [packet.text() for packet in packets])
    return sender, original, packets, video

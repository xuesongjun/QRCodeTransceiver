#!/usr/bin/env python3
"""
QR Code Receiver GUI - 二维码喷泉码接收端图形界面
"""

import queue
import sys
import threading
import time
import zlib
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Optional, Set, Tuple
import tkinter as tk

import cv2
import numpy as np
from mss import mss

from fountain import Glass, str2Droplet

# 压缩标记前缀
COMPRESS_MAGIC = b"ZLIB:"

Detection = Tuple[str, Optional[Tuple[int, int, int, int]]]

# QR 解码器
qrDecoder = cv2.QRCodeDetector()


def qrdecode(image: np.ndarray) -> List[Detection]:
    detections: List[Detection] = []
    try:
        retval, decoded_infos, points, _ = qrDecoder.detectAndDecodeMulti(image)
    except cv2.error:
        retval = False
    if retval:
        for data, pts in zip(decoded_infos, points):
            if not data:
                continue
            xs = pts[:, 0]
            ys = pts[:, 1]
            left = int(xs.min())
            top = int(ys.min())
            right = int(xs.max())
            bottom = int(ys.max())
            detections.append((data, (left, top, right - left, bottom - top)))
    else:
        data, points, _ = qrDecoder.detectAndDecode(image)
        if data:
            detections.append((data, None))
    return detections


def decode_with_fallback(image: np.ndarray) -> List[Detection]:
    detections = qrdecode(image)
    if detections:
        return detections
    inverted = cv2.bitwise_not(image)
    return qrdecode(inverted)


def validate_droplet(droplet_str: str, expected_num_chunks: Optional[int] = None) -> bool:
    """验证 droplet 字符串格式是否正确"""
    try:
        parts = droplet_str.split("|", 3)
        if len(parts) != 4:
            return False
        seed = int(parts[0])
        num_chunks = int(parts[1])
        padding = int(parts[2])
        if seed < 0 or num_chunks <= 0 or padding < 0:
            return False
        if num_chunks > 1000000:
            return False
        if padding > 1024:
            return False
        if expected_num_chunks is not None and num_chunks != expected_num_chunks:
            return False
        import base64
        base64.b64decode(parts[3])
        return True
    except Exception:
        return False


class Decoder:
    """喷泉码解码器"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.glass: Optional[Glass] = None
        self._seeds: Set[int] = set()
        self._completed_files: Set[Tuple[int, int, bytes]] = set()
        self.file_index = 1
        self.last_status = ""

    def reset(self):
        self.glass = None
        self._seeds = set()
        self.last_status = ""

    def feed(self, droplet_str: str) -> Optional[str]:
        """处理一个 droplet，返回保存的文件名或 None"""
        expected = self.glass.num_chunks if self.glass else None
        if not validate_droplet(droplet_str, expected):
            return None

        try:
            droplet = str2Droplet(droplet_str)
        except Exception:
            return None

        if self.glass and droplet.num_chunks != self.glass.num_chunks:
            self.reset()

        if droplet.seed in self._seeds:
            return None

        self._seeds.add(droplet.seed)

        if self.glass is None:
            self.glass = Glass(droplet)
        else:
            self.glass.addDroplet(droplet)

        if self.glass.isDone():
            data = self._assemble_data()
            if self._is_duplicate_file(data):
                self.reset()
                return None
            filename, payload = self._split_payload(data)
            saved_path = self._write_file(filename, payload)
            self._mark_file_completed(data)
            self.reset()
            return saved_path

        return None

    def get_progress(self) -> Tuple[int, int, int]:
        """返回 (已解码块数, 总块数, 已收包数)"""
        if self.glass is None:
            return (0, 0, 0)
        return (self.glass.chunksDone(), self.glass.num_chunks, len(self._seeds))

    def _assemble_data(self) -> bytes:
        assert self.glass is not None
        chunks = list(self.glass.chunks)
        if self.glass.padding:
            chunks[-1] = chunks[-1][:-self.glass.padding]
        return b"".join(chunks)

    def _split_payload(self, data: bytes) -> Tuple[str, bytes]:
        idx = data.find(b"\n")
        if idx != -1:
            raw_name = data[:idx]
            payload = data[idx + 1:]
            name = raw_name.decode("utf-8", errors="ignore").strip()
        else:
            payload = data
            name = ""
        if not name:
            name = f"qr_output_{self.file_index}"
        name = Path(name).name
        # 自动解压
        payload = self._decompress(payload)
        return name, payload

    def _decompress(self, data: bytes) -> bytes:
        if data.startswith(COMPRESS_MAGIC):
            try:
                compressed = data[len(COMPRESS_MAGIC):]
                return zlib.decompress(compressed)
            except zlib.error:
                return data
        return data

    def _write_file(self, filename: str, payload: bytes) -> str:
        target = self.output_dir / filename
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            target = self.output_dir / f"{stem}_{int(time.time())}{suffix}"
        target.write_bytes(payload)
        self.file_index += 1
        return str(target)

    def _is_duplicate_file(self, data: bytes) -> bool:
        import hashlib
        if self.glass is None:
            return False
        content_hash = hashlib.md5(data).digest()
        signature = (self.glass.num_chunks, self.glass.padding, content_hash)
        return signature in self._completed_files

    def _mark_file_completed(self, data: bytes):
        import hashlib
        if self.glass is None:
            return
        content_hash = hashlib.md5(data).digest()
        signature = (self.glass.num_chunks, self.glass.padding, content_hash)
        self._completed_files.add(signature)


class ReceiverApp:
    """接收端 GUI 应用"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("QR Receiver")
        self.root.geometry("280x120")
        self.root.resizable(False, False)

        # 状态
        self.running = False
        self.output_dir = Path("decoded")
        self.decoder: Optional[Decoder] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

        self._build_ui()
        self._update_status("就绪")

    def _build_ui(self):
        # 工具栏
        toolbar = tk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        # 按钮样式
        btn_width = 6

        self.btn_start = tk.Button(toolbar, text="▶ 开始", width=btn_width, command=self._on_start)
        self.btn_start.pack(side=tk.LEFT, padx=2)

        self.btn_stop = tk.Button(toolbar, text="⏹ 停止", width=btn_width, command=self._on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=2)

        self.btn_restart = tk.Button(toolbar, text="🔄 重置", width=btn_width, command=self._on_restart, state=tk.DISABLED)
        self.btn_restart.pack(side=tk.LEFT, padx=2)

        self.btn_folder = tk.Button(toolbar, text="📁 目录", width=btn_width, command=self._on_select_folder)
        self.btn_folder.pack(side=tk.LEFT, padx=2)

        # 进度条
        progress_frame = tk.Frame(self.root)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = tk.Canvas(progress_frame, height=20, bg="white", highlightthickness=1, highlightbackground="gray")
        self.progress_bar.pack(fill=tk.X)

        # 状态栏
        status_frame = tk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=10, pady=2)

        self.status_label = tk.Label(status_frame, text="就绪", anchor="w")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.dir_label = tk.Label(status_frame, text=f"📁 {self.output_dir}", anchor="e", fg="gray")
        self.dir_label.pack(side=tk.RIGHT)

    def _update_status(self, text: str):
        self.status_label.config(text=text)

    def _update_progress(self, done: int, total: int, received: int):
        if total <= 0:
            ratio = 0
        else:
            ratio = done / total

        self.progress_bar.delete("all")
        width = self.progress_bar.winfo_width()
        height = self.progress_bar.winfo_height()

        # 绘制进度条
        fill_width = int(width * ratio)
        if fill_width > 0:
            self.progress_bar.create_rectangle(0, 0, fill_width, height, fill="#4CAF50", outline="")

        # 绘制文字
        percent = int(ratio * 100)
        text = f"{percent}% ({done}/{total}) 已收 {received} 包"
        self.progress_bar.create_text(width // 2, height // 2, text=text, fill="black")

    def _on_start(self):
        if self.running:
            return

        self.running = True
        self.stop_event.clear()
        self.decoder = Decoder(self.output_dir)

        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_restart.config(state=tk.NORMAL)
        self.btn_folder.config(state=tk.DISABLED)

        self._update_status("等待检测二维码...")

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

        self._poll_progress()

    def _on_stop(self):
        self.running = False
        self.stop_event.set()

        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_restart.config(state=tk.DISABLED)
        self.btn_folder.config(state=tk.NORMAL)

        self._update_status("已停止")

    def _on_restart(self):
        if self.decoder:
            self.decoder.reset()
        self._update_status("已重置，等待新的传输...")
        self._update_progress(0, 0, 0)

    def _on_select_folder(self):
        folder = filedialog.askdirectory(initialdir=str(self.output_dir), title="选择接收文件目录")
        if folder:
            self.output_dir = Path(folder)
            self.dir_label.config(text=f"📁 {self.output_dir.name}")

    def _poll_progress(self):
        if not self.running:
            return

        if self.decoder:
            done, total, received = self.decoder.get_progress()
            if total > 0:
                self._update_status(f"接收中...")
                self._update_progress(done, total, received)

        self.root.after(100, self._poll_progress)

    def _worker_loop(self):
        """后台工作线程"""
        with mss() as sct:
            monitors = sct.monitors
            monitor = monitors[1] if len(monitors) > 1 else monitors[0]

            region = {
                "left": monitor["left"],
                "top": monitor["top"],
                "width": monitor["width"],
                "height": monitor["height"]
            }

            while not self.stop_event.is_set():
                try:
                    # 截屏
                    raw = sct.grab(region)
                    frame = np.array(raw)
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)

                    # 解码
                    detections = decode_with_fallback(gray)

                    for barcode, rect in detections:
                        barcode = barcode.strip()
                        if not barcode:
                            continue

                        # 自动缩小区域
                        if rect and region["width"] == monitor["width"]:
                            padding = 50
                            region = {
                                "left": monitor["left"] + rect[0] - padding,
                                "top": monitor["top"] + rect[1] - padding,
                                "width": rect[2] + padding * 2,
                                "height": rect[3] + padding * 2
                            }

                        if self.decoder:
                            saved = self.decoder.feed(barcode)
                            if saved:
                                self.root.after(0, lambda s=saved: self._on_file_saved(s))

                    if not detections:
                        time.sleep(0.02)

                except Exception as e:
                    time.sleep(0.1)

    def _on_file_saved(self, path: str):
        self._update_status(f"已保存: {Path(path).name}")
        self._update_progress(0, 0, 0)
        messagebox.showinfo("文件已保存", f"文件已保存到:\n{path}")

    def run(self):
        self.root.mainloop()


def main():
    app = ReceiverApp()
    app.run()


if __name__ == "__main__":
    main()

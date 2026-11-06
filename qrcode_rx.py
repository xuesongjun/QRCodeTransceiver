"""
qrcode_rx.py — 二维码喷泉码接收端 (RX，命令行版)

功能概述
--------
持续截取屏幕，识别二维码中的喷泉码 droplet，
用置信传播 (BP) 译码后自动还原并保存文件.

快速使用
--------
  # 基本用法 (全屏检测，自动定位二维码区域，保存到 decoded/)
  python qrcode_rx.py

  # 指定截屏区域 (left top width height) ，省去自动定位延迟
  python qrcode_rx.py --region 100 200 800 800

  # 静默模式 (只保留文件保存日志)
  python qrcode_rx.py --quiet

  # 调试模式 (输出每个 droplet 的原始内容)
  python qrcode_rx.py --debug

  # 仅输出 droplet 字符串，不保存文件 (适合管道处理)
  python qrcode_rx.py --no-save

  # 配合发送端高吞吐参数时，可缩短截屏间隔
  python qrcode_rx.py -i 0.01

常用参数
--------
  --monitor N         mss monitor 索引，默认 1 (主屏)
  --region L T W H    指定截屏区域 (像素) ，不填则自动检测
  -i, --interval SEC  截屏间隔秒数，默认 0.02
  --output-dir DIR    接收文件保存目录，默认 decoded
  --dedup             按 seed 去重，跳过已处理的 droplet
  --quiet             静默模式，减少终端输出
  --debug             调试模式，打印每个原始 droplet

解码器优先级
------------
  1. pyzbar (速度更快，需额外安装 zbar 动态库)
  2. OpenCV QRCodeDetector (内置，无需额外安装)
  自动检测，pyzbar 可用时优先使用.
"""

import argparse
import queue
import sys
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import cv2
import numpy as np
from mss import mss

from fountain import Glass, str2Droplet

# TX 端用于传递文件总数的特殊"文件名"，RX 端收到后更新期望总数
MANIFEST_FILENAME = "__FILE_COUNT__"
# 压缩标记前缀 (与 TX 保持一致) ，数据以此开头表示正文需要 zlib 解压
COMPRESS_MAGIC = b"ZLIB:"

# Detection 类型: (解码字符串, 可选的矩形区域 (left, top, width, height))
Detection = Tuple[str, Optional[Tuple[int, int, int, int]]]

# ---------------------------------------------------------------------------
# QR 解码器自动选择 (按性能优先)
# ---------------------------------------------------------------------------
# 策略: 优先用 pyzbar (速度更快) ，不可用时回退到 OpenCV 内置解码器.
# DECODER_NAME 记录最终使用的解码器名称，供启动时打印.
DECODER_NAME = None

# 1. 尝试 pyzbar (需额外安装: pip install pyzbar，Linux 还需 libzbar0)
try:
    import pyzbar.pyzbar as pyzbar

    def qrdecode(image: np.ndarray, multi: bool = False) -> List[Detection]:
        """使用 pyzbar 解码 QR 码，返回 Detection 列表."""
        detections: List[Detection] = []
        for obj in pyzbar.decode(image):
            rect = obj.rect
            detections.append(
                (obj.data.decode("utf-8"), (rect.left, rect.top, rect.width, rect.height))
            )
        return detections

    DECODER_NAME = "pyzbar"
except (ModuleNotFoundError, Exception):
    pass

# 2. 回退到 OpenCV 内置 QR 检测器
if DECODER_NAME is None:
    qrDecoder = cv2.QRCodeDetector()

    def _rect_from_points(points: Optional[np.ndarray]) -> Optional[Tuple[int, int, int, int]]:
        """将 OpenCV 返回的角点数组转换为 (left, top, width, height) 矩形."""
        if points is None or points.size == 0:
            return None
        pts = points.reshape(-1, 2)
        xs = pts[:, 0]
        ys = pts[:, 1]
        left = int(xs.min())
        top = int(ys.min())
        right = int(xs.max())
        bottom = int(ys.max())
        return (left, top, right - left, bottom - top)

    def qrdecode(image: np.ndarray, multi: bool = False) -> List[Detection]:
        """使用 OpenCV 解码 QR 码，multi=True 时尝试多码检测."""
        detections: List[Detection] = []
        if multi:
            try:
                retval, decoded_infos, points, _ = qrDecoder.detectAndDecodeMulti(image)
            except cv2.error:
                retval = False
            if retval:
                for data, pts in zip(decoded_infos, points):
                    if data:
                        detections.append((data, _rect_from_points(pts)))
                return detections

        try:
            data, points, _ = qrDecoder.detectAndDecode(image)
        except cv2.error:
            data = ""
            points = None
        if data:
            detections.append((data, _rect_from_points(points)))
        return detections

    DECODER_NAME = "opencv"


@dataclass
class CaptureRegion:
    """
    屏幕截图区域 (像素坐标) .

    字段
    ----
    left, top : int  左上角坐标 (相对于整个虚拟桌面)
    width, height : int  区域宽高
    """
    left: int
    top: int
    width: int
    height: int

    @classmethod
    def from_monitor(cls, monitor) -> "CaptureRegion":
        """从 mss monitors 列表中的一个 monitor 字典构造区域."""
        return cls(
            left=monitor["left"],
            top=monitor["top"],
            width=monitor["width"],
            height=monitor["height"],
        )

    @classmethod
    def from_tuple(cls, values: Tuple[int, int, int, int]) -> "CaptureRegion":
        """从 (left, top, width, height) 元组构造区域."""
        left, top, width, height = values
        return cls(left=left, top=top, width=width, height=height)

    def to_dict(self):
        """转换为 mss.grab() 所需的字典格式."""
        return dict(left=self.left, top=self.top, width=self.width, height=self.height)

    def clamp(self, monitor) -> "CaptureRegion":
        """将区域裁剪到 monitor 边界内，防止越界截屏."""
        left = max(self.left, monitor["left"])
        top = max(self.top, monitor["top"])
        right = min(self.left + self.width, monitor["left"] + monitor["width"])
        bottom = min(self.top + self.height, monitor["top"] + monitor["height"])
        return CaptureRegion(left=left, top=top, width=max(1, right - left), height=max(1, bottom - top))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="截取屏幕上的二维码喷泉码，输出 droplet 字符串."
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="mss 的 monitor 索引 (默认 1，表示主屏) .",
    )
    parser.add_argument(
        "--region",
        type=int,
        nargs=4,
        metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"),
        help="指定截屏区域 (left top width height) .不填则使用整个屏幕.",
    )
    parser.add_argument(
        "--no-auto-region",
        action="store_true",
        help="禁用自动定位二维码区域 (默认只在未指定 --region 时自动定位一次) .",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=float,
        default=0.02,
        help="每次截屏之间的间隔秒数 (默认 0.02) .",
    )
    parser.add_argument(
        "--dedup",
        action="store_true",
        help="按 seed 去重，避免重复输出同一个 droplet.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式，仅输出 droplet 本身.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="调试模式，输出详细的 droplet 数据.",
    )
    parser.add_argument(
        "--output-dir",
        default="decoded",
        help="自动保存译码文件的目录 (默认 decoded) .",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="仅输出 droplet，不自动译码保存文件.",
    )
    return parser.parse_args()


def capture_frame(sct: mss, region: CaptureRegion) -> np.ndarray:
    raw = sct.grab(region.to_dict())
    # BGRA -> Gray
    frame = np.array(raw)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    return gray


class ScreenCapture:
    """
    多线程后台截屏器，始终持有最新帧，避免主线程被截屏 I/O 阻塞.

    内部维护一个容量为 2 的队列: 截屏线程持续写入新帧，
    队列满时丢弃旧帧，保证 read() 拿到的总是最新截图.

    用法
    ----
    cap = ScreenCapture(region)
    frame = cap.read()          # 阻塞等待最新帧 (超时返回 None)
    cap.update_region(new_reg)  # 动态更新截屏区域
    cap.stop()                  # 停止后台线程
    """

    def __init__(self, region: CaptureRegion, max_queue_size: int = 2):
        self.region = region
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self):
        with mss() as sct:
            while not self._stop_event.is_set():
                try:
                    frame = capture_frame(sct, self.region)
                    # 如果队列满了，丢弃旧帧
                    if self.q.full():
                        try:
                            self.q.get_nowait()
                        except queue.Empty:
                            pass
                    self.q.put(frame)
                except Exception:
                    time.sleep(0.01)

    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def update_region(self, region: CaptureRegion):
        self.region = region

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=1.0)


def extract_seed(droplet: str) -> Optional[int]:
    """从 droplet 字符串中提取 seed 字段 (第一个 | 之前的整数) ."""
    try:
        return int(droplet.split("|", 1)[0])
    except (ValueError, IndexError):
        return None


def parse_droplet_header(droplet_str: str) -> Optional[Tuple[int, int, int]]:
    parts = droplet_str.split("|", 3)
    if len(parts) != 4:
        return None
    try:
        seed = int(parts[0])
        num_chunks = int(parts[1])
        padding = int(parts[2])
    except ValueError:
        return None
    if seed < 0 or num_chunks <= 0 or num_chunks > 1000000 or padding < 0 or padding > 4096:
        return None
    return seed, num_chunks, padding


def validate_droplet(droplet_str: str, expected_num_chunks: Optional[int] = None) -> bool:
    """
    验证 droplet 字符串格式是否合法.

    检查项
    ------
    1. 恰好 4 个字段 (seed|num_chunks|padding|base64)
    2. 各字段类型及取值范围 (seed>=0, 0<num_chunks<=1M, 0<=padding<=4096)
    3. 若提供 expected_num_chunks，校验与当前流一致
    4. base64 载荷可正常解码

    参数
    ----
    droplet_str         : str       待校验的 droplet 字符串
    expected_num_chunks : int|None  已知的分块数，None 表示不检查

    返回
    ----
    bool  合法返回 True，否则 False
    """
    try:
        parts = droplet_str.split("|", 3)
        if len(parts) != 4:
            return False
        seed = int(parts[0])
        num_chunks = int(parts[1])
        padding = int(parts[2])
        # 基本范围检查
        if seed < 0 or num_chunks <= 0 or padding < 0:
            return False
        # num_chunks 上限约 500MB 文件 (500*1024*1024/512 ≈ 1000000)
        if num_chunks > 1000000:
            return False
        # padding 不应该超过发送端 chunk_size；保留上限避免异常数据.
        if padding > 4096:
            return False
        # 如果已有期望的 num_chunks，检查是否一致
        if expected_num_chunks is not None and num_chunks != expected_num_chunks:
            return False
        # 检查 base64 数据部分是否有效
        import base64
        base64.b64decode(parts[3])
        return True
    except Exception:
        return False


def expand_rect(
    rect: Tuple[int, int, int, int],
    base_region: CaptureRegion,
    padding: int = 40,
) -> CaptureRegion:
    """
    将检测到的 QR 码矩形向四周扩展 padding 像素，得到更宽松的截屏区域.

    扩展后锁定到该区域截屏，减少无关背景，提高后续帧的识别速度.
    注意: 扩展后需用 clamp() 裁剪到屏幕边界内.

    参数
    ----
    rect        : (left, top, width, height)  QR 检测结果 (相对于 base_region)
    base_region : CaptureRegion               当前截屏区域 (提供坐标偏移)
    padding     : int                         四周扩展像素数，默认 40
    """
    left, top, width, height = rect
    new_left = base_region.left + left - padding
    new_top = base_region.top + top - padding
    new_width = width + padding * 2
    new_height = height + padding * 2
    return CaptureRegion(left=new_left, top=new_top, width=new_width, height=new_height)


def decode_with_fallback(image: np.ndarray, multi: bool = False) -> List[Detection]:
    """
    先尝试正常图像解码，失败后对图像取反再试一次.

    某些屏幕上 QR 码颜色反转 (白底黑码变黑底白码) 时，
    取反后可提高识别率.
    """
    detections = qrdecode(image, multi=multi)
    if detections:
        return detections
    if not multi:
        detections = qrdecode(image, multi=True)
        if detections:
            return detections
    inverted = cv2.bitwise_not(image)
    detections = qrdecode(inverted, multi=multi)
    if detections or multi:
        return detections
    return qrdecode(inverted, multi=True)


def frame_signature(image: np.ndarray) -> Tuple[Tuple[int, ...], int]:
    return image.shape, zlib.crc32(image.tobytes())


def make_progress_bar(done: int, total: int, width: int = 30) -> str:
    """
    生成文本进度条字符串，用于终端输出.

    示例输出: [████████░░░░░░░░░░░░░░░░░░░░░░] 27% (3/11)

    参数
    ----
    done  : int  已完成数量
    total : int  总数量
    width : int  进度条字符宽度

    返回
    ----
    str  格式化的进度条字符串，total<=0 时返回空字符串
    """
    if total <= 0:
        return ""
    ratio = done / total
    filled = int(width * ratio)
    bar = "█" * filled + "░" * (width - filled)
    percent = int(ratio * 100)
    return f"[{bar}] {percent}% ({done}/{total})"


class DropletAutoSaver:
    """
    喷泉码自动译码器与文件保存器 (命令行版) .

    职责
    ----
    - 接收 droplet 字符串，维护 Glass 译码状态
    - 译码完成后自动解压、还原文件名、写入磁盘
    - 支持多文件: 通过 header 中的 total_files 字段跟踪进度
    - 检测重复文件 (相同内容) 并跳过

    典型用法
    --------
    saver = DropletAutoSaver(Path("decoded"))
    while True:
        droplet_str = scan_qr()          # 从屏幕扫描
        saver.feed(droplet_str)          # 喂给译码器，完成时自动保存
    """

    def __init__(self, output_dir: Path, verbose: bool = True, debug: bool = False):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.debug = debug
        self.file_index = 1
        self.expected_total: Optional[int] = None   # 预期接收文件总数 (来自 manifest)
        self.completed_total = 0                    # 已成功保存的文件数
        # 已完成文件的特征集合，用于跳过重复传输
        # 格式: (num_chunks, padding, md5哈希)
        self._completed_files: Set[Tuple[int, int, bytes]] = set()
        # 多文件进度 (从每个文件 header 中解析)
        self.total_files: int = 0         # 发送端声明的总文件数
        self.received_files: int = 0      # 已接收完成的文件数
        self.current_filename: str = ""   # 当前正在接收的文件名
        self._reset_session()

    def _reset_session(self):
        """重置当前文件的 Glass 译码器和 seed 去重集合，准备接收下一个文件."""
        self.glass: Optional[Glass] = None
        self._seeds: Set[int] = set()  # 已见过的 seed，防止重复喂入同一包

    def _get_file_signature(self, num_chunks: int, padding: int) -> Tuple[int, int]:
        """获取文件流的签名 (用于初步判断是否为同一文件) """
        return (num_chunks, padding)

    def _is_duplicate_file(self, data: bytes) -> bool:
        """检查是否为已完成的重复文件"""
        import hashlib
        if self.glass is None:
            return False
        content_hash = hashlib.md5(data).digest()
        signature = (self.glass.num_chunks, self.glass.padding, content_hash)
        return signature in self._completed_files

    def _mark_file_completed(self, data: bytes):
        """标记文件已完成"""
        import hashlib
        if self.glass is None:
            return
        content_hash = hashlib.md5(data).digest()
        signature = (self.glass.num_chunks, self.glass.padding, content_hash)
        self._completed_files.add(signature)

    def feed(self, droplet_str: str) -> bool:
        """
        处理一个 droplet 字符串，驱动译码并在完成时保存文件.

        流程: 解析包头 → 检测流切换 → 去重 → 解析为 Droplet →
              加入 Glass → 显示进度 → 完成时写文件

        参数
        ----
        droplet_str : str  从 QR 码读到的原始字符串

        返回
        ----
        bool  True 表示本次调用完成了一个文件的接收与保存
        """
        header = parse_droplet_header(droplet_str)
        if header is None:
            if self.debug:
                self._log(f"丢弃无效 droplet: {droplet_str[:50]}...")
            return False

        seed, num_chunks, _ = header
        if self.glass and num_chunks != self.glass.num_chunks:
            self._log("检测到新的 droplet 流，重置译码器。")
            self._reset_session()

        if seed in self._seeds:
            return False

        try:
            droplet = str2Droplet(droplet_str)
        except Exception as exc:
            self._log(f"解析 droplet 失败: {exc}")
            return False

        self._seeds.add(droplet.seed)
        if self.glass is None:
            self.glass = Glass(droplet)
            self._log(f"开始接收文件 (分块数: {self.glass.num_chunks})")
        else:
            self.glass.addDroplet(droplet)
        # 显示进度条
        self._show_progress()
        if self.glass.isDone():
            data = self._assemble_data()
            # 检查是否为重复文件
            if self._is_duplicate_file(data):
                self._log("检测到重复文件，跳过保存.")
                self._reset_session()
                return False
            filename, payload = self._split_payload(data)
            wrote = self._write_file(filename, payload)
            # 标记文件已完成
            self._mark_file_completed(data)
            self._reset_session()
            if wrote:
                self.file_index += 1
                return True
        return False

    def _show_progress(self):
        """显示进度条"""
        if not self.verbose or self.glass is None:
            return
        done = self.glass.chunksDone()
        total = self.glass.num_chunks
        received = len(self._seeds)
        bar = make_progress_bar(done, total)

        # 构建进度信息
        file_info = ""
        if self.total_files > 0:
            file_info = f"[{self.received_files + 1}/{self.total_files}] "

        # 使用 \r 覆盖同一行
        print(f"\r{file_info}接收中: {bar} 新包 {received}", end="", file=sys.stderr, flush=True)
        if done == total:
            print(file=sys.stderr)  # 完成时换行

    def _assemble_data(self) -> bytes:
        assert self.glass is not None
        chunks = list(self.glass.chunks)
        if self.glass.padding:
            chunks[-1] = chunks[-1][:-self.glass.padding]
        return b"".join(chunks)

    def _split_payload(self, data: bytes) -> Tuple[str, bytes]:
        """解析 payload，格式: 文件名|文件编号|总文件数\n数据"""
        idx = data.find(b"\n")
        if idx != -1:
            raw_header = data[:idx]
            payload = data[idx + 1:]
            header = raw_header.decode("utf-8", errors="ignore").strip()

            # 解析新格式: 文件名|文件编号|总文件数
            parts = header.split("|")
            if len(parts) >= 3:
                name = parts[0]
                try:
                    file_index = int(parts[1])
                    total_files = int(parts[2])
                    # 更新多文件进度信息
                    if total_files > 0:
                        self.total_files = total_files
                except ValueError:
                    pass
            else:
                # 兼容旧格式: 只有文件名
                name = header
        else:
            payload = data
            name = ""
        if not name:
            name = f"qr_output_{self.file_index}"
        name = Path(name).name
        self.current_filename = name
        # 自动解压
        payload = self._decompress(payload)
        return name, payload

    def _decompress(self, data: bytes) -> bytes:
        """检测并解压 zlib 压缩的数据"""
        if data.startswith(COMPRESS_MAGIC):
            try:
                compressed = data[len(COMPRESS_MAGIC):]
                decompressed = zlib.decompress(compressed)
                self._log(f"已解压数据: {len(compressed)} -> {len(decompressed)} 字节")
                return decompressed
            except zlib.error as e:
                self._log(f"解压失败: {e}")
                return data
        return data

    def _write_file(self, filename: str, payload: bytes) -> bool:
        if filename == MANIFEST_FILENAME:
            self._handle_manifest(payload)
            return False
        if (
            self.expected_total is not None
            and self.completed_total >= self.expected_total
        ):
            self._log("已达到文件数量上限，忽略额外文件.")
            return False
        target = self.output_dir / filename
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            target = self.output_dir / f"{stem}_{int(time.time())}{suffix}"
        target.write_bytes(payload)
        self.completed_total += 1
        self.received_files += 1

        # 显示文件保存进度
        if self.total_files > 0:
            self._log(f"已保存文件 [{self.received_files}/{self.total_files}]: {target}")
            if self.received_files >= self.total_files:
                self._log(f"✓ 全部 {self.total_files} 个文件接收完成！")
        else:
            self._log(f"已保存文件: {target}")

        if (
            self.expected_total is not None
            and self.completed_total >= self.expected_total
        ):
            self._log("已接收完所有文件.")
        return True

    def _handle_manifest(self, payload: bytes):
        try:
            count = int(payload.decode("utf-8").strip() or "0")
            self.expected_total = count
            self.completed_total = 0
            self._log(f"即将接收 {count} 个文件.")
        except ValueError:
            self._log("文件数量信息解析失败.")

    def _log(self, message: str):
        if self.verbose:
            print(message, file=sys.stderr)


def main():
    """
    命令行入口.

    执行流程
    --------
    1. 解析参数，初始化 DropletAutoSaver (除非 --no-save)
    2. 同步截屏阶段: 全屏扫描，找到第一个 QR 码后记录其坐标
    3. 缩小区域后启动 ScreenCapture 后台线程，进入高速循环
    4. 主循环: 取帧 → 解码 → 去重 → 喂给 saver → 等待 (无检测时)
    5. Ctrl+C 优雅退出，停止后台截屏线程
    """
    args = parse_args()
    if not args.quiet:
        print(f"使用 {DECODER_NAME} 解码二维码", file=sys.stderr)
    seen_seeds: Set[int] = set() if args.dedup else set()
    auto_region_enabled = not args.no_auto_region and args.region is None
    saver: Optional[DropletAutoSaver] = None
    if not args.no_save:
        saver = DropletAutoSaver(
            Path(args.output_dir),
            verbose=not args.quiet,
            debug=args.debug
        )

    screen_cap: Optional[ScreenCapture] = None
    try:
        with mss() as sct:
            monitors = sct.monitors
            monitor_index = min(max(1, args.monitor), len(monitors) - 1)
            monitor_region = CaptureRegion.from_monitor(monitors[monitor_index])
            active_region: Optional[CaptureRegion] = (
                CaptureRegion.from_tuple(tuple(args.region))
                if args.region
                else None
            )
            if not args.quiet:
                if active_region:
                    print(
                        f"截屏区域 ({active_region.width}x{active_region.height})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "等待检测二维码...",
                        file=sys.stderr,
                    )
            lookup_region = active_region or monitor_region

            # 初始阶段使用同步截屏来定位二维码
            while auto_region_enabled and active_region is None:
                gray = capture_frame(sct, lookup_region)
                detections = decode_with_fallback(gray)
                target_detection = next(
                    (d for d in detections if d[1] is not None), None
                )
                if target_detection:
                    rect = expand_rect(target_detection[1], lookup_region)
                    active_region = rect.clamp(monitors[monitor_index])
                    lookup_region = active_region
                    auto_region_enabled = False
                    if not args.quiet:
                        print(
                            f"已定位二维码区域 ({active_region.width}x{active_region.height})",
                            file=sys.stderr,
                        )
                else:
                    time.sleep(args.interval)

            # 定位完成后启动多线程截屏
            screen_cap = ScreenCapture(lookup_region)
            last_frame_signature: Optional[Tuple[Tuple[int, ...], int]] = None

            while True:
                gray = screen_cap.read(timeout=1.0)
                if gray is None:
                    continue
                signature = frame_signature(gray)
                if signature == last_frame_signature:
                    time.sleep(args.interval)
                    continue
                last_frame_signature = signature
                detections = decode_with_fallback(gray, multi=True)
                detected_valid = False
                for barcode, _ in detections:
                    barcode = barcode.strip()
                    if not barcode:
                        continue
                    seed = extract_seed(barcode)
                    if args.dedup and seed is not None:
                        if seed in seen_seeds:
                            continue
                        seen_seeds.add(seed)
                    # 只在 debug 模式下输出原始数据
                    if args.debug:
                        print(barcode, flush=True)
                    if saver:
                        completed = saver.feed(barcode)
                        if completed and args.dedup:
                            seen_seeds.clear()
                        detected_valid = True
                # 只在没有检测到有效数据时才等待
                if not detected_valid:
                    time.sleep(args.interval)
    except KeyboardInterrupt:
        if not args.quiet:
            print("\n用户中断，退出.", file=sys.stderr)
    finally:
        if screen_cap:
            screen_cap.stop()


if __name__ == "__main__":
    main()

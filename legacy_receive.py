"""明确独立的旧视频一次性解析入口, 不保存新任务现场或生成补传码."""

import sys
import time
import zlib
from pathlib import Path
from typing import Optional, Set, Tuple
from fountain import Glass, str2Droplet
from qr_decode import decode_with_fallback
from qrcode_video import VideoScanner

COMPRESS_MAGIC = b"ZLIB:"
MANIFEST_FILENAME = "__FILE_COUNT__"

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
    except (ValueError, IndexError):
        return False


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
        self.saved_files = []
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
        except (ValueError, IndexError) as exc:
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
        return self._save_complete() if self.glass.isDone() else False

    def finish(self):
        """旧视频结束时同样执行完整残余求解, 不把剥皮停滞误作信息不足."""
        if self.glass is None:
            return False
        self.glass.solve()
        return self._save_complete() if self.glass.isDone() else False

    def _save_complete(self):
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
                raise ValueError(f"旧格式文件解压失败: {e}") from e
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
        self.saved_files.append(str(target))
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



def receive_legacy_video(path, output_dir, stop_event, progress=None):
    saver = DropletAutoSaver(output_dir, verbose=False)
    seen = set()
    with VideoScanner(path, decode_with_fallback, lambda text: parse_droplet_header(text) is not None) as scanner:
        for droplets in scanner:
            if stop_event.is_set():
                break
            for text in droplets:
                if text in seen:
                    continue
                seen.add(text)
                saver.feed(text)
            if progress is not None:
                progress(scanner.frame_index, scanner.total_frames, saver)
            if saver.total_files > 0 and saver.received_files >= saver.total_files:
                break
    saver.finish()
    return saver.saved_files

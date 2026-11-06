"""
qrcode_tx.py — 二维码喷泉码发送端 (TX)

功能概述
--------
读取一个或多个文件, 使用喷泉码 (LT 码) 编码后, 以动画形式在屏幕上
循环展示二维码, 供接收端 (qrcode_rx.py / qrcode_rx_gui.py) 扫描.

快速使用
--------
  # 发送单个文件 (默认实时模式, 约 33 fps)
  python qrcode_tx.py myfile.zip

  # 发送多个文件
  python qrcode_tx.py a.txt b.txt c.pdf

  # 通配符 (引号防止 shell 展开)
  python qrcode_tx.py "*.txt"

  # 从文件列表发送
  python qrcode_tx.py -f files.list

  # 预生成模式 (先生成所有图片再播放)
  python qrcode_tx.py bigfile.exe --no-live

  # 仅生成图片, 不显示窗口
  python qrcode_tx.py myfile.zip --no-display

  # 高吞吐参数 (屏幕清晰时可用)
  python qrcode_tx.py myfile.zip --chunk-size 1536 -i 20 --error-correction L

  # 禁用压缩 (已压缩的文件如 .zip .jpg 推荐使用)
  python qrcode_tx.py already_compressed.zip --no-compress

常用参数
--------
  --chunk-size N      每块字节数, 默认 512 (越大 QR 越密, 需要屏幕够大)
  --size N            QR 图片像素边长, 默认 800
  -i, --interval N    播放间隔毫秒, 默认 30 (约 33 fps)
  --error-correction  L/M/Q/H, 等级越高越稳但容量越低, 默认 M
  --extra RATIO       额外生成的 droplet 比例, 默认 0.5 (冗余 50%)

payload 格式 (写入 QR 的字符串)
---------------------------------
  fountain droplet 字符串: seed|num_chunks|padding|base64_data
  其中 base64_data 是若干 chunk XOR 后的载荷.

  每个文件的原始数据在喷泉码编码前, 会预置一行 header:
    文件名|文件编号|总文件数\\n数据
  接收端据此还原文件名和传输进度.
"""

import argparse
import glob
import math
import zlib
from pathlib import Path
from typing import List, Tuple

import qrcode
from qrcode.exceptions import DataOverflowError
from PIL import Image

from fountain import Fountain

# 压缩标记前缀: 数据以此开头表示正文是 zlib 压缩的
COMPRESS_MAGIC = b"ZLIB:"

# QR 纠错等级映射 (L 容量最大, H 最稳定)
ERROR_CORRECTION_LEVELS = {
    "L": qrcode.constants.ERROR_CORRECT_L,  # ~7% 纠错
    "M": qrcode.constants.ERROR_CORRECT_M,  # ~15% 纠错 (默认)
    "Q": qrcode.constants.ERROR_CORRECT_Q,  # ~25% 纠错
    "H": qrcode.constants.ERROR_CORRECT_H,  # ~30% 纠错
}


def build_qr(
    droplet: str,
    size: int = 512,
    border: int = 4,
    error_correction: int = qrcode.constants.ERROR_CORRECT_M,
) -> Image.Image:
    """
    将单个 droplet 字符串编码为 QR 码图片.

    参数
    ----
    droplet          : str   droplet 序列化字符串 (seed|num_chunks|padding|b64)
    size             : int   输出图片的像素边长 (正方形) , 0 表示不缩放
    border           : int   QR 静区宽度 (模块数)
    error_correction : int   纠错等级常量

    返回
    ----
    PIL.Image.Image  RGB 模式的 QR 码图片

    异常
    ----
    ValueError  当 chunk_size 过大导致单个 droplet 超出 QR 容量时抛出
    """
    qr = qrcode.QRCode(
        version=None,          # 自动选择最小版本
        error_correction=error_correction,
        box_size=10,
        border=border,
    )
    qr.add_data(droplet)
    try:
        qr.make(fit=True)
    except DataOverflowError as exc:
        raise ValueError(
            "当前 QR 参数无法容纳单个 droplet, 请降低 --chunk-size "
            "或降低 --error-correction."
        ) from exc
    base_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if size:
        # 使用 NEAREST 插值保持 QR 码锐利边缘
        base_img = base_img.resize((size, size), Image.Resampling.NEAREST)
    return base_img


def display_sequence(entries: List[Tuple[str, str, Image.Image]], interval_ms: int):
    """
    循环播放预生成的二维码序列 (--no-live 模式) .

    将已生成的全部 QR 图片在 tkinter 窗口中轮播, 到达末尾后从头循环.

    参数
    ----
    entries     : list of (filename, droplet_str, PIL.Image)
    interval_ms : int  每张图片的展示时长 (毫秒)
    """
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("QR droplet 播放器")
    root.configure(bg="white")

    # 居中显示
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    img_width = entries[0][2].width if entries else 512
    img_height = entries[0][2].height if entries else 512
    window_width = img_width + 40
    window_height = img_height + 80
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.resizable(False, False)

    label = tk.Label(root, bg="white")
    label.pack(padx=10, pady=10)
    info = tk.Label(root, bg="white", font=("Arial", 12))
    info.pack(pady=(0, 10))

    # 预转换为 tkinter 可用的图片格式 (避免每帧转换)
    tk_imgs = [ImageTk.PhotoImage(img) for _, _, img in entries]
    meta = []
    for name, droplet, _ in entries:
        parts = droplet.split("|", 3)
        seed = parts[0] if len(parts) > 0 else "?"
        num_chunks = parts[1] if len(parts) > 1 else "?"
        meta.append(f"{name} seed={seed} chunks={num_chunks}")

    total = len(entries)
    interval_ms = max(1, int(interval_ms))
    state = {"idx": 0}

    def update():
        idx = state["idx"]
        label.configure(image=tk_imgs[idx])
        info.configure(text=f"{idx+1}/{total} {meta[idx]}")
        state["idx"] = (idx + 1) % total
        root.after(interval_ms, update)

    update()
    root.mainloop()


def display_live(
    fountain: Fountain,
    filename: str,
    size: int,
    border: int,
    interval_ms: int,
    error_correction: int,
):
    """
    单文件实时模式: 持续生成新 droplet 并显示 (不循环固定的包) .

    每次定时回调都调用 fountain.droplet() 生成全新的编码包,
    理论上可以无限生成, 接收端何时扫完何时停止.

    参数
    ----
    fountain         : Fountain  已初始化的发送端对象
    filename         : str       文件名 (仅用于界面显示)
    size             : int       QR 图片像素边长
    border           : int       QR 静区宽度
    interval_ms      : int       刷新间隔 (毫秒)
    error_correction : int       纠错等级常量
    """
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("QR droplet 播放器 (实时)")
    root.configure(bg="white")

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    window_width = size + 40
    window_height = size + 80
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.resizable(False, False)

    label = tk.Label(root, bg="white")
    label.pack(padx=10, pady=10)
    info = tk.Label(root, bg="white", font=("Arial", 12))
    info.pack(pady=(0, 10))

    interval_ms = max(1, int(interval_ms))
    state = {"count": 0, "tk_img": None}

    def update():
        # 每帧生成一个新 droplet
        droplet_str = fountain.droplet().getStr()
        img = build_qr(
            droplet_str,
            size=size,
            border=border,
            error_correction=error_correction,
        )
        state["tk_img"] = ImageTk.PhotoImage(img)
        state["count"] += 1

        parts = droplet_str.split("|", 3)
        seed = parts[0] if len(parts) > 0 else "?"

        label.configure(image=state["tk_img"])
        info.configure(text=f"#{state['count']} {filename} seed={seed} chunks={fountain.num_chunks}")
        root.after(interval_ms, update)

    update()
    root.mainloop()


def parse_args() -> argparse.Namespace:
    """解析命令行参数, 返回 Namespace 对象."""
    parser = argparse.ArgumentParser(
        description="读取文件并生成喷泉码二维码, 支持单文件、多文件和通配符."
    )
    parser.add_argument(
        "filename",
        nargs="*",
        default=[],
        help="需要传输的文件路径, 支持多个文件或通配符如 *.txt."
    )
    parser.add_argument(
        "-f", "--file-list",
        type=str,
        default=None,
        help="文件列表文件, 每行一个文件路径, # 开头为注释."
    )
    parser.add_argument(
        "-o", "--output",
        default="droplets",
        help="二维码输出目录 (--no-live 模式下保存图片, 默认 droplets) .",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=512,
        help="Fountain chunk 大小 (字节) , 默认 512.越大 QR 越密, 需要屏幕够大够清晰.",
    )
    parser.add_argument(
        "--extra",
        type=float,
        default=0.5,
        help="额外生成 droplet 的比例, 默认 0.5 (即再多 50%%) .实时模式下无限生成, 此参数仅影响预生成模式.",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=800,
        help="输出二维码图片边长 (像素) , 默认 800.太小会降低识别率.",
    )
    parser.add_argument(
        "--border",
        type=int,
        default=4,
        help="二维码静区宽度 (模块数) , 默认 4.",
    )
    parser.add_argument(
        "-i", "--interval",
        type=int,
        default=30,
        dest="display_interval",
        help="播放间隔毫秒, 默认 30 (约 33 fps) .间隔太短 RX 来不及处理会丢包.",
    )
    parser.add_argument(
        "--error-correction",
        choices=sorted(ERROR_CORRECTION_LEVELS),
        default="M",
        help="QR 纠错等级, L/M/Q/H, 等级越高越稳但单包容量越低, 默认 M.",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="仅生成图片到 --output 目录, 不弹出轮播窗口 (需配合 --no-live) .",
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="禁用实时模式, 先预生成所有 QR 图片, 再循环播放固定的包.",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="禁用 zlib 自动压缩.对 .zip/.jpg 等已压缩文件可跳过此步骤.",
    )
    return parser.parse_args()


def get_file_list(args) -> List[Path]:
    """
    根据命令行参数收集所有待传输文件, 去重并保持顺序.

    支持三种来源 (可同时使用) :
    1. -f/--file-list 指定的文件列表 (每行一个路径)
    2. 命令行直接给出的文件路径
    3. 命令行通配符 (如 "*.txt")

    参数
    ----
    args : argparse.Namespace

    返回
    ----
    list[Path]  去重后的文件路径列表
    """
    files = []

    # 来源 1: 从文件列表读取
    if args.file_list:
        list_path = Path(args.file_list)
        if not list_path.is_file():
            raise FileNotFoundError(f"找不到文件列表: {list_path}")
        with open(list_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):  # 跳过注释和空行
                    p = Path(line)
                    if p.is_file():
                        files.append(p)
                    else:
                        print(f"警告: 文件不存在, 跳过: {line}")

    # 来源 2 & 3: 命令行参数 (含通配符)
    for filename in args.filename:
        if "*" in filename or "?" in filename:
            # 通配符展开 (Python 端展开, 避免 shell 不展开的情况)
            matched = glob.glob(filename, recursive=True)
            for m in matched:
                p = Path(m)
                if p.is_file():
                    files.append(p)
            if not matched:
                print(f"警告: 通配符 {filename} 没有匹配到任何文件")
        else:
            p = Path(filename)
            if p.is_file():
                files.append(p)
            else:
                print(f"警告: 文件不存在, 跳过: {filename}")

    # 去重并保持原始顺序
    seen = set()
    unique_files = []
    for f in files:
        f_resolved = f.resolve()
        if f_resolved not in seen:
            seen.add(f_resolved)
            unique_files.append(f)

    return unique_files


def prepare_file_data(file_path: Path, no_compress: bool) -> Tuple[bytes, str]:
    """
    读取文件并按需压缩, 返回处理后的字节数据和压缩情况描述.

    压缩策略: 压缩后体积 < 原始 95% 才采用压缩, 否则直接使用原始数据.
    数据以 COMPRESS_MAGIC 前缀标记, 接收端据此自动解压.

    参数
    ----
    file_path   : Path  源文件路径
    no_compress : bool  True 则跳过压缩

    返回
    ----
    (data: bytes, compress_info: str)
      data          处理后的字节数据 (可能含 COMPRESS_MAGIC 前缀)
      compress_info 人类可读的压缩结果描述 (用于终端输出)
    """
    data = file_path.read_bytes()
    original_size = len(data)
    compress_info = ""

    if not no_compress:
        compressed = zlib.compress(data, level=9)
        if len(compressed) < len(data) * 0.95:
            data = COMPRESS_MAGIC + compressed
            ratio = len(data) / original_size * 100
            compress_info = f", 压缩后 {len(data)} 字节 ({ratio:.1f}%)"
        else:
            compress_info = ", 压缩无效已跳过"

    return data, compress_info


def display_live_multi(
    fountains: List[Tuple[Fountain, str]],
    size: int,
    border: int,
    interval_ms: int,
    error_correction: int,
):
    """
    多文件实时模式: 轮流从各文件的 Fountain 生成 droplet 并展示.

    每帧切换到下一个文件, 循环往复, 直到用户关闭窗口.
    接收端无需关心文件顺序, 喷泉码保证只要收足够多的包即可恢复.

    参数
    ----
    fountains        : list of (Fountain, filename)
    size             : int   QR 图片像素边长
    border           : int   QR 静区宽度
    interval_ms      : int   刷新间隔 (毫秒)
    error_correction : int   纠错等级常量
    """
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title(f"QR droplet 播放器 (实时, {len(fountains)} 个文件)")
    root.configure(bg="white")

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    window_width = size + 40
    window_height = size + 80
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.resizable(False, False)

    label = tk.Label(root, bg="white")
    label.pack(padx=10, pady=10)
    info = tk.Label(root, bg="white", font=("Arial", 10))
    info.pack(pady=(0, 10))

    interval_ms = max(1, int(interval_ms))
    state = {"file_idx": 0, "count": 0, "tk_img": None}

    def update():
        file_idx = state["file_idx"]
        fountain, filename = fountains[file_idx]

        # 实时生成该文件的下一个 droplet
        droplet_str = fountain.droplet().getStr()
        img = build_qr(
            droplet_str,
            size=size,
            border=border,
            error_correction=error_correction,
        )
        state["tk_img"] = ImageTk.PhotoImage(img)
        state["count"] += 1

        parts = droplet_str.split("|", 3)
        seed = parts[0] if len(parts) > 0 else "?"

        label.configure(image=state["tk_img"])
        info.configure(text=f"[{file_idx+1}/{len(fountains)}] {filename} #{state['count']} seed={seed} chunks={fountain.num_chunks}")

        # 轮换到下一个文件
        state["file_idx"] = (file_idx + 1) % len(fountains)
        root.after(interval_ms, update)

    update()
    root.mainloop()


def build_payload(name: str, body: bytes, file_index: int = 1, total_files: int = 1) -> bytes:
    """
    构建喷泉码 payload, 在数据前写入 header 行.

    格式:   文件名|文件编号|总文件数\\n数据字节
    示例:   myfile.zip|1|3\\n<binary data>

    接收端 (_split_payload) 解析此格式, 还原文件名及多文件进度.

    参数
    ----
    name        : str   原始文件名 (不含路径)
    body        : bytes 文件内容 (已压缩或原始)
    file_index  : int   当前文件在批次中的编号 (从 1 开始)
    total_files : int   本批次总文件数

    返回
    ----
    bytes  拼接了 header 的完整 payload
    """
    header = f"{name}|{file_index}|{total_files}\n".encode("utf-8")
    return header + body


def main():
    args = parse_args()

    # 至少需要文件路径或文件列表之一
    if not args.filename and not args.file_list:
        print("错误: 请指定文件路径或使用 -f 指定文件列表")
        print("用法: python qrcode_tx.py <文件...> 或 python qrcode_tx.py -f file.list")
        print("      python qrcode_tx.py *.txt  # shell 会自动展开通配符")
        return

    files = get_file_list(args)
    if not files:
        print("错误: 没有找到任何文件")
        return

    print(f"准备传输 {len(files)} 个文件:")
    for f in files:
        print(f"  - {f.name}")
    print()
    error_correction = ERROR_CORRECTION_LEVELS[args.error_correction]

    # 为所有文件构建 Fountain 对象
    fountains: List[Tuple[Fountain, str]] = []
    total_files = len(files)
    for file_idx, file_path in enumerate(files, start=1):
        data, compress_info = prepare_file_data(file_path, args.no_compress)
        payload = build_payload(file_path.name, data, file_idx, total_files)
        fountain = Fountain(payload, chunk_size=args.chunk_size)

        original_size = file_path.stat().st_size
        print(f"[{file_idx}/{total_files}] {file_path.name} 大小 {original_size} 字节{compress_info}, 分块数 {fountain.num_chunks}")

        fountains.append((fountain, file_path.name))

    # 默认路径: 实时模式 (每帧实时生成新 droplet, 节省内存)
    if not args.no_live and not args.no_display:
        print()
        print("实时模式启动, 循环播放所有文件...")
        display_live_multi(
            fountains,
            args.size,
            args.border,
            args.display_interval,
            error_correction,
        )
        return

    # 预生成模式: 先把所有 droplet 图片生成到磁盘
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    display_entries: List[Tuple[str, str, Image.Image]] = []

    for fountain, filename in fountains:
        # 生成 num_chunks * (1 + extra) 个 droplet, 保证足够冗余
        total_droplets = fountain.num_chunks + math.ceil(fountain.num_chunks * args.extra)
        print(f"生成 {filename} 的 {total_droplets} 个二维码...")

        for idx in range(total_droplets):
            droplet = fountain.droplet().getStr()
            img = build_qr(
                droplet,
                size=args.size,
                border=args.border,
                error_correction=error_correction,
            )
            img.save(out_dir / f"{filename}_droplet_{idx+1:05d}.png")
            if not args.no_display:
                display_entries.append((filename, droplet, img.copy()))

    print(f"已输出到目录: {out_dir.resolve()}")
    if display_entries and not args.no_display:
        display_sequence(display_entries, args.display_interval)


if __name__ == "__main__":
    main()

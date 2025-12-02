import argparse
import math
import zlib
from pathlib import Path
from typing import List, Tuple

import qrcode
from PIL import Image

from FF import Fountain

# 压缩标记前缀
COMPRESS_MAGIC = b"ZLIB:"


def build_qr(droplet: str, size: int = 512, border: int = 4) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=10,
        border=border,
    )
    qr.add_data(droplet)
    qr.make(fit=True)
    base_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if size:
        # 将二维码缩放到指定尺寸
        base_img = base_img.resize((size, size), Image.Resampling.NEAREST)
    return base_img


def display_sequence(entries: List[Tuple[str, str, Image.Image]], interval_ms: int):
    """循环播放预生成的二维码序列"""
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("QR droplet 播放器")
    root.configure(bg="white")

    # 获取屏幕尺寸
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    # 获取图片尺寸
    img_width = entries[0][2].width if entries else 512
    img_height = entries[0][2].height if entries else 512

    # 计算窗口尺寸（包含边距和信息标签）
    window_width = img_width + 40
    window_height = img_height + 80

    # 计算居中位置
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2

    # 设置窗口位置
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.resizable(False, False)

    label = tk.Label(root, bg="white")
    label.pack(padx=10, pady=10)
    info = tk.Label(root, bg="white", font=("Arial", 12))
    info.pack(pady=(0, 10))

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


def display_live(fountain: Fountain, filename: str, size: int, border: int, interval_ms: int):
    """实时生成新 droplet 并显示（不循环固定的包）"""
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("QR droplet 播放器 (实时)")
    root.configure(bg="white")

    # 获取屏幕尺寸
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    # 计算窗口尺寸
    window_width = size + 40
    window_height = size + 80

    # 计算居中位置
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2

    # 设置窗口位置
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.resizable(False, False)

    label = tk.Label(root, bg="white")
    label.pack(padx=10, pady=10)
    info = tk.Label(root, bg="white", font=("Arial", 12))
    info.pack(pady=(0, 10))

    interval_ms = max(1, int(interval_ms))
    state = {"count": 0, "tk_img": None}

    def update():
        # 生成新的 droplet
        droplet_str = fountain.droplet().getStr()
        img = build_qr(droplet_str, size=size, border=border)
        state["tk_img"] = ImageTk.PhotoImage(img)
        state["count"] += 1

        # 解析 seed
        parts = droplet_str.split("|", 3)
        seed = parts[0] if len(parts) > 0 else "?"

        label.configure(image=state["tk_img"])
        info.configure(text=f"#{state['count']} {filename} seed={seed} chunks={fountain.num_chunks}")
        root.after(interval_ms, update)

    update()
    root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="读取单个文件并生成喷泉码二维码（纯白背景，内容居中）。"
    )
    parser.add_argument("filename", help="需要传输的文件路径。")
    parser.add_argument(
        "-o",
        "--output",
        default="droplets",
        help="二维码输出目录（默认 droplets）。",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1024,
        help="Fountain chunk 大小，默认 1024 字节。",
    )
    parser.add_argument(
        "--extra",
        type=float,
        default=0.5,
        help="额外生成 droplet 的比例（默认 0.5，即再多 50%%）。",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=512,
        help="输出二维码图片边长（默认 512 像素）。",
    )
    parser.add_argument(
        "--border",
        type=int,
        default=4,
        help="二维码静区宽度（默认 4 个模块）。",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=100,
        dest="display_interval",
        help="播放间隔毫秒数（默认 100，即每秒 10 帧）。",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="仅生成图片，不弹出轮播窗口。",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="实时模式：不断生成新的 droplet，而不是循环播放固定的包。",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="禁用自动压缩（默认会使用 zlib 压缩数据）。",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    input_path = Path(args.filename)
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到文件：{input_path}")

    data = input_path.read_bytes()
    original_size = len(data)

    # 压缩数据
    compress_info = ""
    if not args.no_compress:
        compressed = zlib.compress(data, level=9)
        # 只有压缩后更小才使用压缩
        if len(compressed) < len(data) * 0.95:  # 至少节省 5%
            data = COMPRESS_MAGIC + compressed
            ratio = len(data) / original_size * 100
            compress_info = f"，压缩后 {len(data)} 字节 ({ratio:.1f}%)"
        else:
            compress_info = "，压缩无效已跳过"

    payload = build_payload(input_path.name, data)
    fountain = Fountain(payload, chunk_size=args.chunk_size)
    total_chunks = fountain.num_chunks

    # 实时模式：直接开始播放，不预生成
    if args.live and not args.no_display:
        print(
            f"文件 {input_path.name} 大小 {original_size} 字节{compress_info}，"
            f"分块数 {total_chunks}，实时模式启动。"
        )
        display_live(fountain, input_path.name, args.size, args.border, args.display_interval)
        return

    # 预生成模式
    total_droplets = total_chunks + math.ceil(total_chunks * args.extra)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"文件 {input_path.name} 大小 {original_size} 字节{compress_info}，"
        f"分块数 {total_chunks}，将生成 {total_droplets} 个二维码。"
    )

    display_entries: List[Tuple[str, str, Image.Image]] = []
    for idx in range(total_droplets):
        droplet = fountain.droplet().getStr()
        img = build_qr(droplet, size=args.size, border=args.border)
        img.save(out_dir / f"droplet_{idx+1:05d}.png")
        if not args.no_display:
            display_entries.append((input_path.name, droplet, img.copy()))

    print(f"已输出到目录：{out_dir.resolve()}")
    if display_entries and not args.no_display:
        display_sequence(display_entries, args.display_interval)


def build_payload(name: str, body: bytes) -> bytes:
    header = name.encode("utf-8") + b"\n"
    return header + body


if __name__ == "__main__":
    main()

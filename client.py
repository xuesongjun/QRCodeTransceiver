import argparse
import math
from pathlib import Path
from typing import List, Tuple

import qrcode
from PIL import Image

from FF import Fountain


def build_qr(droplet: str, size: int = 512, border: int = 4) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=10,
        border=border,
    )
    qr.add_data(droplet)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if size:
        img = img.resize((size, size), Image.NEAREST)
    return img


def display_sequence(entries: List[Tuple[str, str, Image.Image]], interval_ms: int):
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("qrjs droplet 播放器")
    root.configure(bg="white")
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
        default=512,
        help="Fountain chunk 大小，默认 512 字节。",
    )
    parser.add_argument(
        "--extra",
        type=float,
        default=0.2,
        help="额外生成 droplet 的比例（默认 0.2，即再多 20%%）。",
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
        "--display-interval",
        type=int,
        default=200,
        help="轮播二维码的间隔（毫秒，默认 200）。",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="仅生成图片，不弹出轮播窗口。",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    input_path = Path(args.filename)
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到文件：{input_path}")

    data = input_path.read_bytes()
    payload = build_payload(input_path.name, data)
    fountain = Fountain(payload, chunk_size=args.chunk_size)
    total_chunks = fountain.num_chunks
    total_droplets = total_chunks + math.ceil(total_chunks * args.extra)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"文件 {input_path.name} 大小 {len(data)} 字节，"
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

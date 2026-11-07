"""发送命令行: 冻结任务、定向补传、导出一轮二维码或打开播放器."""

import argparse
import math
import sys
import time
from pathlib import Path

from qr_render import ERROR_CORRECTION_LEVELS, build_qr
from transfer_state import SendTask


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filename', nargs='?', type=Path, help='一个文件; 多文件请先打成 ZIP')
    parser.add_argument('--task', type=Path, help='已有 .qrtx 发送任务, 或新任务保存位置')
    parser.add_argument('--repair', help='单行补传码或 .qrreq 请求文件路径')
    parser.add_argument('--chunk-size', type=int, default=256)
    parser.add_argument('--no-compress', action='store_true')
    parser.add_argument('--extra', type=float, default=0.5)
    parser.add_argument('--size', type=int, default=600)
    parser.add_argument('--border', type=int, default=4)
    parser.add_argument('--error-correction', choices=ERROR_CORRECTION_LEVELS, default='M')
    parser.add_argument('-i', '--interval', type=int, default=150)
    parser.add_argument('--no-display', action='store_true', help='导出一轮 PNG 后退出')
    parser.add_argument('-o', '--output', type=Path, default=Path('droplets'))
    args = parser.parse_args(argv)
    if args.extra < 0 or not math.isfinite(args.extra) or args.interval < 1 or args.size < 100 or args.border < 4:
        parser.error('冗余应非负, 显示间隔为正, 尺寸至少 100, 静区至少 4')
    try:
        if args.filename:
            if args.task and args.task.exists():
                raise ValueError('新文件不能覆盖已有发送任务; 继续任务请只传 --task')
            task = SendTask.create(args.filename, args.chunk_size, not args.no_compress)
            task.save(args.task or Path('tasks') / f'{task.parameters.task_id[:16]}_{time.time_ns()}.qrtx')
        elif args.task:
            task = SendTask.load(args.task)
        else:
            parser.error('请指定文件, 或用 --task 打开已有发送任务')
        requested = None
        if args.repair:
            code = args.repair if args.repair.startswith('QRF1:') else Path(args.repair).read_text(encoding='utf-8')
            feedback = task.request(code)
            requested = feedback.requested
            print(f'补传 {len(requested)} 个源块, {len(requested) * task.parameters.chunk_size} 字节载荷, {feedback.description}')
        print(f"{task.manifest['filename']}: {task.parameters.num_chunks} 个源块. 冻结任务: {task.state_path}")
        if requested == ():
            print('请求没有待补传源块.')
            return 0
        if args.no_display:
            if args.output.exists() and any(args.output.iterdir()):
                raise ValueError('二维码输出目录必须为空, 避免混入其他任务')
            args.output.mkdir(parents=True, exist_ok=True)
            for packet, offset, total in task.rounds(requested, args.extra):
                build_qr(packet.text(), args.size, args.border, ERROR_CORRECTION_LEVELS[args.error_correction]).save(args.output / f'{offset:08d}.png')
                if offset == total:
                    break
        else:
            import tkinter as tk
            from qrcode_tx_gui import SenderApp
            root = tk.Tk()
            app = SenderApp(root, task)
            app.requested = requested
            app.interval.set(str(args.interval))
            app.size.set(str(args.size))
            app.extra = args.extra
            app.border = args.border
            app.correction = ERROR_CORRECTION_LEVELS[args.error_correction]
            app.start()
            root.mainloop()
        return 0
    except (OSError, ValueError) as exc:
        print(f'发送失败: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

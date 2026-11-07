"""接收命令行: 导入视频, 或先录制屏幕再逐帧解析; 支持续收和补传请求."""

import argparse
import signal
import sys
import threading
from pathlib import Path

import cv2
from mss.exception import ScreenShotError

from receive_pipeline import process_video
from screen_record import record_screen
from transfer_state import ReceiveTask, atomic_write


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--video', type=Path)
    source.add_argument('--record-screen', type=Path, help='录制文件保存路径, Ctrl+C 停止录制并解析')
    parser.add_argument('--fps', type=float, default=30)
    parser.add_argument('--seconds', type=float)
    parser.add_argument('--monitor', type=int, default=1)
    parser.add_argument('--task', type=Path, help='继续已有 .qrrx, 或指定新现场保存路径')
    parser.add_argument('--output-dir', type=Path, default=Path('received'))
    parser.add_argument('--request-out', type=Path, help='导出单行补传码文件, 同时打印补传码')
    parser.add_argument('--request-mode', choices=('auto', 'missing', 'success', 'bitmap', 'all'), default='auto')
    parser.add_argument('--legacy', action='store_true', help='仅一次性解析旧视频, 不续收/补传')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    if not (args.video or args.record_screen or args.task):
        parser.error('请选择 --video、--record-screen 或 --task')
    if args.legacy and (not args.video or args.task or args.request_out):
        parser.error('旧格式入口仅支持 --video, 不支持现场或补传')
    stop = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda signum, frame: stop.set())
    try:
        if args.legacy:
            from legacy_receive import receive_legacy_video
            files = receive_legacy_video(args.video, args.output_dir, stop)
            print(f'旧视频一次性解析完成, 保存 {len(files)} 个文件. 无任务标识, 不支持续收/补传.', file=sys.stderr)
            return 0 if files else 2
        task = (ReceiveTask.load(args.task) if args.task and
                (args.task.exists() or not (args.video or args.record_screen)) else None)
        video = args.video
        if args.record_screen:
            count, elapsed = record_screen(args.record_screen, stop, args.fps, args.monitor, args.seconds)
            print(f'视频已保存: {args.record_screen}. {count} 帧 / {elapsed:.1f} 秒, 实际 {count / elapsed:.1f} 帧/秒.', file=sys.stderr)
            stop.clear()
            video = args.record_screen
        if video:
            result = process_video(video, task, args.output_dir, stop, args.task)
            task = result.task
            print(result.text(), file=sys.stderr)
            if result.error:
                return 1
        elif task is not None and task.integrity_error:
            raise ValueError(task.integrity_error)
        if task is None:
            print('未找到有效任务包. 旧视频请使用 --legacy.', file=sys.stderr)
            return 2
        if args.request_out:
            feedback = task.feedback(args.request_mode)
            atomic_write(args.request_out, (feedback.code + '\n').encode('ascii'))
            task.save(task.state_path)
            print(f'{task.label}: 总源块 {task.parameters.num_chunks}, 已恢复 {task.done}, 未恢复 {len(task.missing)}; {feedback.description}, {len(feedback.code)} 字符. 现场已保存. 将补传码复制到发送端.', file=sys.stderr)
            print(feedback.code)
        return 130 if stop.is_set() else 0 if task.verified else 2
    except (OSError, ValueError, cv2.error, ScreenShotError) as exc:
        print(f'接收失败: {exc}', file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == '__main__':
    raise SystemExit(main())

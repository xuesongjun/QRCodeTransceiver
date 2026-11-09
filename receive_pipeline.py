"""UI/CLI 共用的逐帧接收、任务续收、最终校验和现场保存流程."""

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from qr_decode import decode_with_fallback
from qrcode_video import VideoScanner
from transfer_protocol import Packet, PacketError, is_transfer_text
from transfer_state import ReceiveTask, file_digest


@dataclass
class RoundResult:
    task: object
    frames: int = 0
    new_blocks: int = 0
    state_saved: bool = False
    error: str = ''
    duplicate_video: bool = False
    cancelled: bool = False
    solving: dict = field(default_factory=dict)

    def text(self):
        if self.task is None:
            if self.cancelled:
                return '已停止解析, 尚未建立接收任务; 视频文件仍保留, 可稍后重新导入.'
            return self.error or '没有找到 QT2 任务包. 旧视频请使用独立的一次性解析入口.'
        task = self.task
        status = ('文件校验通过并已保存' if task.verified and task.output_path else
                  '源块已齐, 最终校验或文件保存未完成' if not task.missing else '仍有未恢复源块')
        saved = f'现场已保存: {task.state_path}' if self.state_saved else '现场未保存成功'
        prefix = '重复视频, 保留原进度. ' if self.duplicate_video else ''
        solving = (f"本轮结束求解: 高斯新增恢复 {self.solving['gaussian']} 块, "
                   f"求解前残余方程秩 {self.solving['rank']}/{self.solving['unknowns']}. "
                   if self.solving else '')
        return (f'{prefix}{task.label}: 总源块 {task.parameters.num_chunks}, 已恢复 {task.done}, '
                f'未恢复 {len(task.missing)}, 本轮新增 {self.new_blocks}. {status}. {saved}. '
                + solving
                + (f'错误: {self.error}. ' if self.error else '')
                + (f'无效编码包累计 {task.invalid_packets}, 重复编码包累计 {task.duplicate_packets}. ')
                + (f'异常原因: {task.errors}. ' if task.errors else '')
                + ('可打开恢复文件.' if task.verified and task.output_path else
                   '下一步: 查看校验/保存错误, 保留现场.' if not task.missing else
                   '下一步: 追加视频或生成补传码, 复制到发送端.'))


def process_video(path: Path, task, output_dir: Path, stop_event, state_path=None, overwrite=False, progress=None):
    """不按视频速度等待; 每个有效包立即消元, 结束/取消/读取失败时保存已有进度."""
    result = RoundResult(task)
    before = task.done if task is not None else 0
    fingerprint = None
    try:
        fingerprint = file_digest(path)
        if task is not None and fingerprint in task.videos:
            result.duplicate_video = True
        else:
            last_update = 0.0
            with VideoScanner(path, decode_with_fallback, is_transfer_text) as scanner:
                for texts in scanner:
                    if stop_event.is_set():
                        result.cancelled = True
                        break
                    result.frames = scanner.frame_index
                    for text in texts:
                        if task is None:
                            try:
                                packet = Packet.read(text)
                            except PacketError:
                                continue
                            task = ReceiveTask(packet.parameters)
                            result.task = task
                        task.feed(text)
                    if progress is not None and time.monotonic() - last_update >= 0.1:
                        progress(scanner.frame_index, scanner.total_frames, task)
                        last_update = time.monotonic()
                    if task is not None and task.glass.isDone():
                        task.finish()
                        break
            if task is not None and not result.cancelled:
                task.videos.add(fingerprint)
        if task is not None:
            result.solving = task.finish()
            if task.verified:
                task.write_output(output_dir, overwrite)
    except (OSError, ValueError, cv2.error) as exc:
        result.error = str(exc)
    if task is not None:
        result.new_blocks = task.done - before
        task.log('视频解析结束', video=str(path), total=task.parameters.num_chunks, restored=task.done,
                 missing=len(task.missing), new_blocks=result.new_blocks, error=result.error,
                 file=task.label, verified=task.verified, invalid_packets=task.invalid_packets,
                 cancelled=result.cancelled, duplicate_video=result.duplicate_video,
                 solving=result.solving,
                 next_step='打开恢复文件' if task.verified and task.output_path else '追加视频或复制补传码到发送端')
        destination = state_path or task.state_path or output_dir / 'tasks' / f'{task.parameters.task_id[:16]}_{time.time_ns()}.qrrx'
        try:
            task.save(destination)
            result.state_saved = True
        except OSError as exc:
            task.log('现场保存失败', path=str(destination), error=str(exc))
            result.error = (result.error + '; ' if result.error else '') + f'接收现场保存失败: {exc}'
    return result

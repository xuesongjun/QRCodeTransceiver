"""发送端小窗口: 冻结任务、粘贴补传码和带轮次进度的二维码播放器."""

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from qr_render import ERROR_CORRECTION_LEVELS, build_qr
from transfer_state import SendTask


class SenderApp:
    def __init__(self, root, task=None):
        self.root, self.task = root, task
        self.requested = None
        self.worker = None
        self.stop_event = threading.Event()
        self.frames, self.events = queue.Queue(maxsize=2), queue.Queue()
        self.player = None
        self.closing = False
        self.extra, self.border, self.correction = 0.5, 4, ERROR_CORRECTION_LEVELS['M']
        root.title('二维码发送端')
        root.minsize(660, 370)
        root.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(root, padding=14)
        body.pack(fill='both', expand=True)
        self.buttons = []
        row = ttk.Frame(body)
        row.pack(fill='x')
        for label, command in [('新建发送任务', self.new), ('打开原发送任务', self.open), ('另存发送任务', self.save)]:
            button = ttk.Button(row, text=label, command=command)
            button.pack(side='left', padx=3)
            self.buttons.append(button)
        self.info = tk.StringVar()
        ttk.Label(body, textvariable=self.info, wraplength=650).pack(fill='x', pady=10)
        ttk.Label(body, text='补传时先打开原发送任务, 再粘贴补传码或导入请求文件:').pack(anchor='w')
        self.code = tk.StringVar()
        ttk.Entry(body, textvariable=self.code).pack(fill='x', pady=5)
        row = ttk.Frame(body)
        row.pack(fill='x')
        for label, command in [('导入请求文件', self.import_request), ('校验并使用补传码', self.apply_request), ('发送完整任务', self.full)]:
            button = ttk.Button(row, text=label, command=command)
            button.pack(side='left', padx=3)
            self.buttons.append(button)
        settings = ttk.Frame(body)
        settings.pack(fill='x', pady=12)
        self.interval, self.size = tk.StringVar(value='150'), tk.StringVar(value='600')
        ttk.Label(settings, text='每码显示毫秒').pack(side='left')
        ttk.Entry(settings, textvariable=self.interval, width=7).pack(side='left', padx=5)
        ttk.Label(settings, text='二维码边长').pack(side='left')
        ttk.Entry(settings, textvariable=self.size, width=7).pack(side='left', padx=5)
        self.start_button = ttk.Button(settings, text='开始发送', command=self.start)
        self.start_button.pack(side='left', padx=10)
        self.buttons.append(self.start_button)
        ttk.Button(settings, text='停止', command=self.stop).pack(side='left')
        self.status = tk.StringVar(value='先选择文件建立任务. 多文件可先打成 ZIP.')
        ttk.Label(body, textvariable=self.status, wraplength=650).pack(fill='x')
        self.refresh()
        root.after(80, self.poll)

    def refresh(self):
        if self.task is None:
            self.info.set('尚未建立发送任务')
        else:
            count = self.task.parameters.num_chunks if self.requested is None else len(self.requested)
            self.info.set(f"{self.task.manifest['filename']} | 任务 {self.task.parameters.task_id[:16]}\n"
                          f'总源块 {self.task.parameters.num_chunks}; 本次发送 {count} 个源块, {count * self.task.parameters.chunk_size} 字节块载荷.\n保存任务: {self.task.state_path}')

    def busy(self, value):
        for button in self.buttons:
            button.configure(state='disabled' if value else 'normal')

    def new(self):
        name = filedialog.askopenfilename(title='选择发送文件; 多文件请先打成 ZIP')
        if not name:
            return
        target = filedialog.asksaveasfilename(title='保存冻结的发送任务, 请保留用于补传', defaultextension='.qrtx', initialfile=Path(name).name + '.qrtx')
        if not target:
            return
        self.busy(True)
        self.status.set('正在读取并冻结文件, 成功保存任务后即可发送...')
        def prepare():
            try:
                task = SendTask.create(Path(name))
                task.save(Path(target))
                self.events.put(('task', task))
            except (OSError, ValueError) as exc:
                self.events.put(('error', str(exc)))
        self.worker = threading.Thread(target=prepare)
        self.worker.start()

    def open(self):
        name = filedialog.askopenfilename(filetypes=[('发送任务', '*.qrtx')])
        if name:
            try:
                self.task = SendTask.load(Path(name))
                self.requested = None
                self.refresh()
                self.status.set('已打开冻结任务, 补传不会重新读取源文件.')
            except (OSError, ValueError) as exc:
                messagebox.showerror('无法打开任务', str(exc))

    def save(self):
        if self.task is None:
            return
        name = filedialog.asksaveasfilename(defaultextension='.qrtx')
        if name:
            try:
                self.task.save(Path(name))
                self.refresh()
                self.status.set('发送任务已保存.')
            except OSError as exc:
                messagebox.showerror('保存失败', str(exc))

    def import_request(self):
        name = filedialog.askopenfilename(filetypes=[('补传请求', '*.qrreq'), ('所有文件', '*')])
        if name:
            try:
                self.code.set(Path(name).read_text(encoding='utf-8').strip())
                self.apply_request()
            except (OSError, UnicodeError) as exc:
                messagebox.showerror('请求文件读取失败', str(exc))

    def apply_request(self):
        if self.task is None:
            messagebox.showinfo('先打开任务', '请打开首次发送时保存的 .qrtx 任务.')
            return
        try:
            feedback = self.task.request(self.code.get())
            self.requested = feedback.requested
            self.refresh()
            self.status.set(f'校验通过: {feedback.description}, {len(feedback.code)} 字符; '
                            f'将补发 {len(self.requested)} 个源块. 点击开始发送.')
        except ValueError as exc:
            messagebox.showerror('补传请求无效', str(exc))

    def full(self):
        self.requested = None
        self.refresh()
        self.status.set('已选择完整任务. 每轮覆盖全部源块, 随后发送新的喷泉修复包.')

    @staticmethod
    def _produce(task, requested, size, frames, events, stop, extra=0.5, border=4, correction=ERROR_CORRECTION_LEVELS['M']):
        try:
            for packet, offset, total in task.rounds(requested, extra):
                if stop.is_set():
                    break
                entry = (build_qr(packet.text(), size, border, correction), packet.round_no, offset, total)
                while not stop.is_set():
                    try:
                        frames.put(entry, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except (OSError, ValueError) as exc:
            events.put(('error', str(exc)))

    def start(self):
        if self.task is None:
            messagebox.showinfo('先建立任务', '请新建发送任务或打开原发送任务.')
            return
        if self.requested == ():
            messagebox.showinfo('无需补传', '请求中没有待补传源块.')
            return
        try:
            interval, size = int(self.interval.get()), int(self.size.get())
            if interval < 1 or not 200 <= size <= min(self.root.winfo_screenheight() - 160, self.root.winfo_screenwidth() - 80):
                raise ValueError('显示间隔须为正整数; 二维码边长应至少 200 且能完整放入屏幕.')
        except ValueError as exc:
            messagebox.showerror('显示参数有误', str(exc))
            return
        self.interval_ms = interval
        self.next_frame = 0
        self.frames = queue.Queue(maxsize=2)
        self.stop_event.clear()
        self.busy(True)
        self.player = tk.Toplevel(self.root)
        self.player.title(self.task.manifest['filename'])
        self.player.configure(bg='white')
        self.player.protocol('WM_DELETE_WINDOW', self.stop)
        self.image_label = tk.Label(self.player, bg='white')
        self.image_label.pack(padx=12, pady=8)
        self.round_label = tk.Label(self.player, bg='white')
        self.round_label.pack()
        self.bar = tk.Canvas(self.player, width=size, height=12, bg='#e5e7eb', highlightthickness=0)
        self.bar.pack(padx=12, pady=8)
        self.worker = threading.Thread(target=self._produce, args=(self.task, self.requested, size, self.frames, self.events, self.stop_event, self.extra, self.border, self.correction))
        self.worker.start()
        self.status.set('正在发送; 录制从第 1 码到本轮末尾的完整一轮. 下一轮有不同编号和颜色.')

    def stop(self):
        self.stop_event.set()
        if self.player is not None:
            self.player.destroy()
            self.player = None
        self.status.set('正在停止并保留发送任务...')

    def close(self):
        self.closing = True
        self.stop()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'task':
                self.task, self.requested = value, None
                self.refresh()
                self.status.set('冻结任务已保存, 可以开始发送.')
            else:
                self.stop()
                self.status.set('失败: ' + value)
                messagebox.showerror('发送失败', value)
        if self.worker is not None and not self.worker.is_alive():
            self.worker = None
            self.busy(False)
        if self.closing and self.worker is None:
            self.root.destroy()
            return
        if self.player is not None and time.monotonic() >= self.next_frame:
            try:
                frame, round_no, offset, total = self.frames.get_nowait()
                self.photo = ImageTk.PhotoImage(frame)
                self.image_label.configure(image=self.photo)
                self.round_label.configure(text=f'第 {round_no} 轮 | {offset}/{total} | ' + ('本轮开始' if offset == 1 else '本轮结束, 下一个是新一轮' if offset == total else '传输中'))
                self.bar.delete('all')
                self.bar.create_rectangle(0, 0, frame.width * offset / total, 12, fill=('#2563eb', '#16a34a', '#9333ea')[(round_no - 1) % 3], outline='')
                self.next_frame = time.monotonic() + self.interval_ms / 1000
            except queue.Empty:
                pass
        self.root.after(25, self.poll)


def main():
    root = tk.Tk()
    if '--self-test' in sys.argv:
        root.withdraw()
        SenderApp(root)
        image = ImageTk.PhotoImage(Image.new('RGB', (2, 2)))
        root.update()
        root.destroy()
        return
    SenderApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()

"""接收端小窗口: 视频/屏幕录制、续收现场、源块图和可复制补传请求."""

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from mss.exception import ScreenShotError

from block_map import cells
from legacy_receive import receive_legacy_video
from receive_pipeline import process_video
from screen_record import record_screen
from transfer_state import ReceiveTask, atomic_write


class ReceiverApp:
    def __init__(self, root):
        self.root, self.task = root, None
        self.worker = None
        self.events = queue.Queue()
        self.stop_event, self.close_event = threading.Event(), threading.Event()
        self.selected = set()
        self.map_cells = []
        self.feedback_code = ''
        self.saved = True
        root.title('二维码接收端')
        root.minsize(850, 720)
        root.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(root, padding=12)
        body.pack(fill='both', expand=True)
        self.actions = []
        def button(parent, label, command):
            item = ttk.Button(parent, text=label, command=command)
            item.pack(side='left', padx=3, pady=3)
            self.actions.append(item)
        row = ttk.Frame(body)
        row.pack(fill='x')
        button(row, '新建/重置任务', self.new)
        button(row, '继续原任务', self.open)
        button(row, '保存现场', self.save)
        button(row, '另存现场', lambda: self.save(True))
        self.output = tk.StringVar(value=str(Path('received').resolve()))
        row = ttk.Frame(body)
        row.pack(fill='x')
        ttk.Label(row, text='接收目录').pack(side='left')
        ttk.Entry(row, textvariable=self.output).pack(side='left', fill='x', expand=True, padx=6)
        button(row, '选择目录', self.directory)
        row = ttk.Frame(body)
        row.pack(fill='x')
        button(row, '导入/追加 MP4', self.import_video)
        button(row, '录制屏幕', self.record)
        self.stop_button = ttk.Button(row, text='停止录制并解析 / 停止解析', command=self.stop_event.set)
        self.stop_button.pack(side='left', padx=3)
        button(row, '旧视频一次性解析', lambda: self.import_video(True))
        row = ttk.Frame(body)
        row.pack(fill='x')
        self.fps, self.monitor = tk.StringVar(value='30'), tk.StringVar(value='1')
        ttk.Label(row, text='录制帧/秒').pack(side='left')
        ttk.Entry(row, textvariable=self.fps, width=6).pack(side='left', padx=5)
        ttk.Label(row, text='屏幕编号').pack(side='left')
        ttk.Entry(row, textvariable=self.monitor, width=4).pack(side='left', padx=5)
        ttk.Label(row, text='录制时不识别; 停止并保存 MP4 后逐帧解析.').pack(side='left', padx=5)
        self.status = tk.StringVar(value='导入视频建立新任务, 或点击“继续原任务”恢复现场.')
        ttk.Label(body, textvariable=self.status, wraplength=820).pack(fill='x', pady=6)
        self.progress = ttk.Progressbar(body)
        self.progress.pack(fill='x')
        self.counts = tk.StringVar(value='尚无任务')
        ttk.Label(body, textvariable=self.counts, wraplength=820).pack(fill='x', pady=5)
        middle = ttk.Frame(body)
        middle.pack(fill='x')
        self.canvas = tk.Canvas(middle, width=310, height=250, bg='white', highlightthickness=0)
        self.canvas.pack(side='left')
        self.canvas.bind('<Button-1>', self.select_cell)
        self.canvas.bind('<Double-Button-1>', self.cell_details)
        ttk.Label(middle, text='灰: 尚未恢复\n黄: 本组部分恢复\n绿: 本组全部恢复\n红: 本轮仍待补传或明确校验异常\n\n单击选择区域; 双击查看块范围/缺块编号.\n显示分组不改变真实分块.\n编号从 0 开始.', justify='left').pack(side='left', padx=15)
        row = ttk.Frame(body)
        row.pack(fill='x')
        self.mode = tk.StringVar(value='自动最短')
        self.modes = {'自动最短': 'auto', '缺块模式': 'missing', '成功模式': 'success', '位图模式': 'bitmap', '全部重发': 'all'}
        ttk.Combobox(row, textvariable=self.mode, values=list(self.modes), state='readonly', width=12).pack(side='left')
        button(row, '生成补传码', self.generate)
        button(row, '从选中区域生成', lambda: self.generate(True))
        button(row, '清除选区', self.clear_selection)
        self.code = tk.StringVar()
        ttk.Entry(body, textvariable=self.code, state='readonly').pack(fill='x', pady=5)
        row = ttk.Frame(body)
        row.pack(fill='x')
        button(row, '复制单行补传码', self.copy)
        button(row, '导出补传请求', self.export)
        self.logs = tk.Text(body, height=7, wrap='word', state='disabled')
        self.logs.pack(fill='both', expand=True, pady=5)
        root.after(100, self.poll)

    def append_log(self, text):
        self.logs.configure(state='normal')
        self.logs.insert('end', text + '\n')
        self.logs.see('end')
        self.logs.configure(state='disabled')

    def busy(self, value):
        for action in self.actions:
            action.configure(state='disabled' if value else 'normal')

    def directory(self):
        name = filedialog.askdirectory()
        if name:
            self.output.set(name)

    def save(self, choose=False):
        if self.task is None:
            return True
        path = self.task.state_path
        if choose or path is None:
            name = filedialog.asksaveasfilename(defaultextension='.qrrx', initialfile=self.task.parameters.task_id[:16] + '.qrrx')
            if not name:
                return False
            path = Path(name)
        try:
            self.task.save(path)
            self.saved = True
            self.status.set(f'现场已保存: {path}')
            self.append_log(f'{self.task.label}: 已恢复 {self.task.done}/{self.task.parameters.num_chunks}, 未恢复 {len(self.task.missing)}. 现场已保存: {path}')
            return True
        except OSError as exc:
            self.saved = False
            self.status.set(f'现场保存失败: {exc}. 请更换可写目录后重试.')
            messagebox.showerror('现场未保存', str(exc))
            return False

    def new(self):
        if not self.save():
            return
        self.task = None
        self.selected.clear()
        self.code.set('')
        self.feedback_code = ''
        self.draw([])
        self.counts.set('尚无任务')
        self.status.set('已新建空任务. 原任务仍保存在原现场文件中; 下一次导入将建立新任务.')

    def open(self):
        if not self.save():
            return
        name = filedialog.askopenfilename(filetypes=[('接收现场', '*.qrrx')])
        if name:
            try:
                self.task = ReceiveTask.load(Path(name))
                if self.task.verified:
                    self.task.write_output(Path(self.output.get()))
                self.task.save(Path(name))
                self.saved = True
                self.selected.clear()
                self.code.set('')
                self.feedback_code = ''
                self.update_task()
                self.logs.configure(state='normal')
                self.logs.delete('1.0', 'end')
                self.logs.configure(state='disabled')
                for event in self.task.events[-30:]:
                    self.append_log(self.event_text(event))
                if self.task.integrity_error:
                    next_step = self.task.integrity_error
                elif self.task.verified:
                    next_step = f'文件校验通过并已保存: {self.task.output_path}'
                else:
                    next_step = '可追加视频或生成最新补传码.'
                self.status.set('现场已恢复并保存. ' + next_step)
            except (OSError, ValueError) as exc:
                self.saved = False
                self.status.set(f'恢复或保存现场失败: {exc}')
                messagebox.showerror('恢复或保存现场失败', str(exc))

    def update_task(self):
        if self.task is not None:
            self.counts.set(f'{self.task.label} | 任务 {self.task.parameters.task_id[:16]} | 总源块 {self.task.parameters.num_chunks}, 已恢复 {self.task.done}, 未恢复 {len(self.task.missing)}')
            self.draw(cells(self.task))

    def event_text(self, event):
        action = event['action']
        label = event.get('file', self.task.label)
        counts = (f"总源块 {event['total']}, 已恢复 {event['restored']}, 未恢复 {event['missing']}"
                  if 'total' in event else '')
        if action == '视频解析结束':
            return (f"{label}: {action}, {counts}, 本轮新增 {event['new_blocks']}. "
                    f"{event['error'] or '解析轮次结束'}. 下一步: {event['next_step']}.")
        if action == '生成补传请求':
            return (f"{label}: {counts}; 请求 {event['requested']} 个源块, "
                    f"{event['mode']}, {event['code_length']} 字符. 请复制补传码到发送端.")
        if action == '现场已保存':
            return f"{label}: {counts}. 现场已保存: {event['path']}"
        return f"{label}: {action}: {event['error']}"

    def draw(self, states):
        self.map_cells = states
        self.canvas.delete('all')
        palette = {'gray': '#b8bec7', 'yellow': '#f5c84b', 'green': '#48b879', 'red': '#ed8882'}
        for index, cell in enumerate(states):
            x, y = index % 10 * 30, index // 10 * 24
            # 选区只影响反馈范围, 不改变任何接收状态.
            self.canvas.create_rectangle(x + 2, y + 2, x + 28, y + 22, fill=palette[cell.color], outline='#111827' if index in self.selected else 'white', width=3 if index in self.selected else 1)

    def select_cell(self, event):
        index = event.y // 24 * 10 + event.x // 30
        if 0 <= index < len(self.map_cells):
            if index in self.selected:
                self.selected.remove(index)
            else:
                self.selected.add(index)
            self.draw(self.map_cells)

    def cell_details(self, event):
        index = event.y // 24 * 10 + event.x // 30
        if 0 <= index < len(self.map_cells):
            cell = self.map_cells[index]
            window = tk.Toplevel(self.root)
            window.title(f'源块 {cell.start} - {cell.end - 1}')
            text = tk.Text(window, width=70, height=16, wrap='word')
            text.pack(fill='both', expand=True)
            text.insert('end', f'范围 [{cell.start}, {cell.end}), 已恢复 {cell.restored}/{cell.end - cell.start}\n{cell.reason}\n未恢复块编号:\n' + ', '.join(map(str, cell.missing)))
            text.configure(state='disabled')

    def clear_selection(self):
        self.selected.clear()
        self.draw(self.map_cells)

    def generate(self, selected=False):
        if self.task is None:
            messagebox.showinfo('暂无任务', '请先导入视频或恢复接收现场.')
            return
        scope = None
        if selected:
            if not self.selected:
                messagebox.showinfo('选择区域', '先单击分块图中需要补传的区域.')
                return
            if self.modes[self.mode.get()] == 'all':
                messagebox.showinfo('选择描述方式', '全部重发覆盖整个任务; 区域补传请选择自动、缺块、成功或位图模式.')
                return
            scope = {block for index in self.selected for block in range(self.map_cells[index].start, self.map_cells[index].end)}
        try:
            feedback = self.task.feedback(self.modes[self.mode.get()], scope)
            if self.task.verified:
                self.task.write_output(Path(self.output.get()))
            self.feedback_code = feedback.code
            self.code.set(feedback.code)
            saved = self.save()
            if feedback.requested:
                next_step = '下一步: 复制单行补传码到发送端, 打开原发送任务后补发; 再追加补传视频.'
            elif self.task.verified:
                next_step = f'无需补传. 文件校验通过并已保存: {self.task.output_path}'
            else:
                next_step = '所选区域无需补传; 任务其他区域仍有未恢复源块, 可选择其他区域或追加视频.'
            text = (f'{self.task.label}: 总源块 {self.task.parameters.num_chunks}, 已恢复 {self.task.done}, 未恢复 {len(self.task.missing)}. '
                    f'请求 {len(feedback.requested)} 个源块, {feedback.description}, {len(feedback.code)} 字符. '
                    + ('现场已保存. ' if saved else '现场未保存成功. ')
                    + next_step)
            self.status.set(text)
            self.append_log(text)
            self.update_task()
        except (OSError, ValueError) as exc:
            messagebox.showerror('不能生成补传码', str(exc))

    def copy(self):
        if self.feedback_code:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.feedback_code)
            self.status.set(f'已复制单行补传码, {len(self.feedback_code)} 字符. 请粘贴到发送端的原任务.')

    def export(self):
        if not self.feedback_code:
            return
        name = filedialog.asksaveasfilename(defaultextension='.qrreq')
        if name:
            try:
                atomic_write(Path(name), (self.feedback_code + '\n').encode('ascii'))
                self.append_log(f'补传请求已导出: {name}, {len(self.feedback_code)} 字符.')
            except OSError as exc:
                messagebox.showerror('请求导出失败', str(exc))

    def import_video(self, legacy=False):
        name = filedialog.askopenfilename(title='旧视频一次性解析' if legacy else '导入/追加同一任务的视频', filetypes=[('视频', '*.mp4 *.mkv *.avi *.mov'), ('所有文件', '*')])
        if name:
            self.launch(Path(name), legacy=legacy)

    def record(self):
        try:
            fps, monitor = float(self.fps.get()), int(self.monitor.get())
            if not 1 <= fps <= 120 or monitor < 1:
                raise ValueError('录制帧率为 1-120, 屏幕编号为正整数.')
        except ValueError as exc:
            messagebox.showerror('录制参数有误', str(exc))
            return
        name = filedialog.asksaveasfilename(title='录制后保留这个 MP4', defaultextension='.mp4')
        if name:
            self.launch(Path(name), recording=(fps, monitor))

    def launch(self, video, legacy=False, recording=None):
        self.stop_event.clear()
        self.saved = False if self.task is not None else True
        self.code.set('')
        self.feedback_code = ''
        self.busy(True)
        output = Path(self.output.get())
        task = self.task
        self.status.set('正在录制屏幕, 点击停止后保存并解析...' if recording else '正在顺序解析视频, 处理慢也不会跳过未处理帧...')
        def work():
            try:
                if recording:
                    count, elapsed = record_screen(video, self.stop_event, *recording, overwrite=True)
                    self.events.put(('log', f'屏幕视频已保存: {video}; {count} 帧 / {elapsed:.1f} 秒, 实际 {count / elapsed:.1f} 帧/秒.'))
                    if self.close_event.is_set():
                        self.events.put(('done', None))
                        return
                    self.stop_event.clear()
                    self.events.put(('status', '录制已保存, 正在使用同一套逐帧解析流程...'))
                if legacy:
                    files = receive_legacy_video(video, output, self.stop_event)
                    self.events.put(('log', f'旧视频一次性解析结束: 保存 {len(files)} 个文件. 未并入新任务, 不提供现场/补传.'))
                    self.events.put(('done', None))
                else:
                    def progress(frame, total, current):
                        self.events.put(('progress', (frame, total, current.done if current else 0, current.parameters.num_chunks if current else 0, cells(current) if current else [])))
                    result = process_video(video, task, output, self.stop_event, progress=progress)
                    self.events.put(('done', result))
            except (OSError, ValueError, cv2.error, ScreenShotError) as exc:
                self.events.put(('error', str(exc)))
        self.worker = threading.Thread(target=work)
        self.worker.start()

    def close(self):
        if self.worker is not None:
            self.close_event.set()
            self.stop_event.set()
            self.status.set('正在停止并保存现场/录制视频, 完成后关闭...')
        elif self.save():
            self.root.destroy()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'progress':
                frame, total, done, chunks, states = value
                self.progress.configure(maximum=max(total, 1), value=frame)
                self.counts.set(f'视频帧 {frame}/{total}; 已恢复源块 {done}/{chunks}')
                self.draw(states)
            elif kind in ('status', 'log'):
                self.status.set(value)
                if kind == 'log':
                    self.append_log(value)
            elif kind == 'error':
                self.status.set('处理失败: ' + value)
                self.append_log('失败: ' + value)
                self.close_event.clear()
                messagebox.showerror('处理失败', value)
            elif value is not None:
                self.task, self.saved = value.task, value.state_saved
                self.status.set(value.text())
                self.append_log(value.text())
                self.update_task()
                if value.error:
                    self.close_event.clear()
                    messagebox.showerror('处理未成功', value.text())
        if self.worker is not None and not self.worker.is_alive():
            self.worker = None
            self.busy(False)
            if self.close_event.is_set():
                if self.save():
                    self.root.destroy()
                    return
                self.close_event.clear()
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    if '--self-test' in sys.argv:
        root.withdraw()
        ReceiverApp(root)
        root.update()
        root.destroy()
        return
    ReceiverApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()

"""有真实 Tk 环境的窗口工作流验收: 生成/复制/导出请求, 发送端校验和视频追加."""

import tempfile
import threading
import time
import sys
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qrcode_rx_gui import ReceiverApp
from qrcode_tx_gui import SenderApp
from repair_request import read
from transfer_protocol import Packet, SOURCE
from transfer_state import ReceiveTask
from video_samples import samples


def main():
    root = tk.Tk()
    root.withdraw()
    sender_root = tk.Toplevel(root)
    sender_root.withdraw()
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'build', prefix='ui-check-') as temp:
        folder = Path(temp)
        sender, body, video, partial = samples(folder)
        sender.save(folder / 'send.qrtx')
        task = ReceiveTask(sender.parameters)
        task.feed(Packet(sender.parameters, SOURCE, 0, 1, sender.source(0)).text())
        task.finish()
        task.save(folder / 'receive.qrrx')
        app = ReceiverApp(root)
        app.task = task
        app.output.set(str(folder / 'received'))
        app.update_task()
        app.generate()
        app.copy()
        code = root.clipboard_get()
        assert code == app.feedback_code and '\n' not in code
        request_file = folder / 'request.qrreq'
        with patch('qrcode_rx_gui.filedialog.asksaveasfilename', return_value=str(request_file)):
            app.export()
        assert request_file.read_text().strip() == code
        tx = SenderApp(sender_root, sender)
        tx.code.set(code)
        tx.apply_request()
        assert tuple(task.missing) == tx.requested
        app.selected = {len(app.map_cells) - 1}
        app.generate(True)
        result = read(app.feedback_code, sender.parameters.task_id, sender.parameters.num_chunks)
        assert result.requested == (sender.parameters.num_chunks - 1,)
        app.launch(video)
        deadline = time.monotonic() + 45
        while app.worker is not None and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert app.worker is None
        assert app.task.verified and app.saved
        assert Path(app.task.output_path).read_bytes() == body
        assert len(app.map_cells) == sender.parameters.num_chunks
        assert all(cell.color == 'green' for cell in app.map_cells)
        assert app.code.get() == ''
    root.destroy()
    print('PASS: 双端窗口生成/复制/导出/校验补传码, 逐帧恢复并重绘源块图')


if __name__ == '__main__':
    main()

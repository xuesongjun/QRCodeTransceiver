# Linux 发送端必须在 Linux 上构建, 保留终端以输入文件路径和显示错误.
from pathlib import Path

root = Path(SPECPATH).parent
sender = Analysis([str(root / 'qrcode_tx.py')], pathex=[str(root)])
exe = EXE(PYZ(sender.pure), sender.scripts, [], exclude_binaries=True,
          name='QRSend', console=True, upx=False)
bundle = COLLECT(exe, sender.binaries, sender.datas, name='QRCodeSender', upx=False)

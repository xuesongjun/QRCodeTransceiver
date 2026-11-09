# 两种系统均提供发送窗口和命令行入口, 共享 Python/Pillow/Tk 运行库.
from pathlib import Path
import sys
from PyInstaller.depend.bindepend import resolve_library_path

root = Path(SPECPATH).parent
binaries = []
if sys.platform == 'linux':
    # Tk 的 X11 客户端依赖, PyInstaller 默认将它视作系统库而排除.
    path = resolve_library_path('libxcb.so.1')
    if path is None:
        raise SystemExit('构建机缺少 libxcb.so.1')
    binaries.append((path, '.'))
gui = Analysis([str(root / 'qrcode_tx_gui.py')], pathex=[str(root)], binaries=binaries, hiddenimports=['PIL._tkinter_finder'])
cli = Analysis([str(root / 'qrcode_tx.py')], pathex=[str(root)], binaries=binaries, hiddenimports=['PIL._tkinter_finder'])
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='QRSend', console=False, upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
              name='QRSendCLI', console=True, upx=False)
bundle = COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
                 name='QRCodeSender', upx=False)

# Windows GUI 和命令行入口共用一个 _internal 目录, 均包含视频模块和 OpenCV 的视频库.
from pathlib import Path

root = Path(SPECPATH).parent
gui = Analysis([str(root / 'qrcode_rx_gui.py')], pathex=[str(root)], excludes=['pyzbar'])
cli = Analysis([str(root / 'qrcode_rx.py')], pathex=[str(root)], excludes=['pyzbar'])
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='QRReceiver', console=False, upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
              name='QRReceiveCLI', console=True, upx=False)
bundle = COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
                 name='QRCodeReceiver', upx=False)

# Windows/Linux GUI 和命令行入口共享运行库, 包含视频模块和 OpenCV 的视频库.
from pathlib import Path
import sys
from PyInstaller.depend.bindepend import resolve_library_path

root = Path(SPECPATH).parent
binaries = []
if sys.platform == 'linux':
    # 固定基线的 X11/OpenGL 分派运行库; 不打包显卡驱动或 glibc.
    for name in ('libxcb.so.1', 'libxcb-randr.so.0', 'libxcb-render.so.0', 'libxcb-shm.so.0',
                 'libxcb-xfixes.so.0', 'libGL.so.1', 'libGLX.so.0', 'libGLdispatch.so.0'):
        path = resolve_library_path(name)
        if path is None:
            raise SystemExit(f'构建机缺少 {name}')
        binaries.append((path, '.'))
hooks = [str(root / 'packaging' / 'rthook_receiver.py')]
gui = Analysis([str(root / 'qrcode_rx_gui.py')], pathex=[str(root)], binaries=binaries, runtime_hooks=hooks, excludes=['pyzbar'])
cli = Analysis([str(root / 'qrcode_rx.py')], pathex=[str(root)], binaries=binaries, runtime_hooks=hooks, excludes=['pyzbar'])
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='QRReceiver', console=False, upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
              name='QRReceiveCLI', console=True, upx=False)
bundle = COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
                 name='QRCodeReceiver', upx=False)

"""让冻结的 Linux MSS 从交付包定位 XCB, 无需目标机 ldconfig/开发工具."""

import sys

if sys.platform == 'linux':
    import ctypes.util
    from pathlib import Path

    _system_find_library = ctypes.util.find_library
    _bundled = {'xcb': 'libxcb.so.1', 'xcb-randr': 'libxcb-randr.so.0',
                'xcb-render': 'libxcb-render.so.0', 'xcb-shm': 'libxcb-shm.so.0',
                'xcb-xfixes': 'libxcb-xfixes.so.0'}

    def _find_library(name):
        if name in _bundled:
            return str(Path(sys._MEIPASS) / _bundled[name])
        return _system_find_library(name)

    ctypes.util.find_library = _find_library

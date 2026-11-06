# 构建和离线分发

## 交付边界

- Windows 接收成品在 Windows x64 + CPython 3.12 构建; Linux 原生成品须在 Linux 上构建.
- PyInstaller 不能在 Windows 上直接构建 Linux 可执行文件. Linux glibc 原生成品应在最老的目标发行版上构建并实测.
- Linux 源码包不含解释器; 可在 Windows 生成. 离线 wheels 依赖明确的 Python 版本、架构和 glibc.
- 版本固定在 `requirements-tx.txt`, `requirements-rx.txt`, `requirements-build.txt`; 更新这些版本后须重新构建并验收.
- 每个包带源码 SHA-256 和构建环境 `BUILD-INFO.json`, ZIP 旁带 `.sha256`. 这用于复核来源, 不承诺不同构建时间得到逐字节相同的 ZIP.

## Windows 接收成品

在 Windows 上, 从项目根目录执行:

```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-rx.txt -r requirements-build.txt
.\.venv-build\Scripts\python.exe build_exe.py --target windows-receiver
```

输出 `dist/QRCodeReceiver-Windows-x64.zip`. 包内包含 GUI、命令行入口、Python/Tk、OpenCV、NumPy、MSS 和视频解码所需的随包库.
`fountain.py` 和 `qrcode_video.py` 作为 Python 模块由 PyInstaller 收集, 不再当作额外数据文件手工复制.
采用目录包, 完整解压后无需另装 Python. 不要删掉 `_internal`.

本机快速验收:

```powershell
.\dist\native\QRCodeReceiver\QRReceiveCLI.exe --help
Start-Process .\dist\native\QRCodeReceiver\QRReceiver.exe -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden
.\dist\native\QRCodeReceiver\QRReceiveCLI.exe --video recording.mp4 --output-dir .\received
```

GUI 自检实际创建 Tk 控件后关闭, 退出码应为 `0`; MP4 验收需逐字节比较恢复文件与原文件.
正式分发前仍需在不安装 Python 的干净 Windows 目标机验收. `--self-test` 是维护入口, 普通用户直接双击即可.

安装完整开发依赖后, 可以对 ZIP 成品运行自动验收, 会解压到新的临时目录并移除子进程的 Python 环境变量:

```powershell
python tests/smoke_receiver_package.py dist/QRCodeReceiver-Windows-x64.zip
```

## Linux 发送源码包

任意系统在项目根目录运行:

```bash
python build_exe.py --target linux-sender-source
```

输出 `dist/QRCodeSender-Linux-source.zip`. 其中只放发送所需源码、安装/启动脚本、固定依赖和说明,
不携带当前开发机器的 venv 或旧的 `offline_packages`.

## 制作 Linux 发送离线包

先在 A 记录 `python3 --version`, `uname -m`, `getconf GNU_LIBC_VERSION`.
下面示例只适用于 A 为 CPython 3.12 + x86_64 + glibc >= 2.17; 若 A 是 3.10/3.11 或 aarch64, 必须修改对应参数.
在有网的 Windows 或 Linux 电脑执行:

```bash
python download_packages.py --platform manylinux2014_x86_64 --python-version 3.12
python build_exe.py --target linux-sender-source --wheel-dir offline_packages/sender-manylinux2014_x86_64-cp312
```

输出 `dist/QRCodeSender-manylinux2014_x86_64-cp312-offline.zip`.
下载器要求空目录, pip 失败会直接报错, 不会把半成品标成成功.
`manifest.json` 记录目标和全部 wheels 的摘要; 分发前和安装前均校验, 安装前额外核对 Linux/Python/CPU/glibc.
如果离线包需求不同, 使用新的目录, 不要向已完成的 wheelhouse 混入其他版本.

系统 Python、Tk、venv 和桌面环境由目标 Linux 发行版提供, 不在 wheelhouse 中.
系统组件的离线安装包应由管理员按目标发行版及版本准备; Windows 的 wheels 下载器不会为它们生成通用安装包.

## Linux 原生发送成品

在符合目标系统的 Linux 图形环境, 安装 Python/Tk/venv 后执行:

```bash
python3 -m venv .venv-build
.venv-build/bin/python -m pip install -r requirements-tx.txt -r requirements-build.txt
.venv-build/bin/python build_exe.py --target linux-sender
```

输出 `dist/QRCodeSender-Linux-x64.zip` (ARM 使用对应架构标签). 解压后运行 `./QRCodeSender/QRSend file.zip`.
这一步在 Windows 环境不执行, 也不能把 Linux 源码 ZIP 宣称为已测试的原生 Linux 成品.
Linux 原生成品仍依赖系统 glibc、图形服务及相关动态库, 必须在实际目标机验收.

## 回归测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试覆盖 MP4 位移、旋转、丢失后定位、去重、文件还原和失败分支, 以及离线清单及压缩包结构.
真实摄像头视频的识别率还需使用目标摄像头、屏幕和录制参数验证.
本次实际构建与测试范围见 [VALIDATION.md](VALIDATION.md).

参考: [PyInstaller 打包模式](https://pyinstaller.org/en/stable/operating-mode.html),
[pip 跨平台下载参数](https://pip.pypa.io/en/stable/cli/pip_download/).

# 四种交付目标的构建

## 目标和形式

`build_exe.py --target` 接受四个值:
`windows-sender`、`windows-receiver`、`linux-sender`、`linux-receiver`.
不加 `--source` 构建便携成品, 加 `--source` 生成源码包.
源码包可附加 `--wheel-dir` 形成离线库包. 旧的仅 Linux 发送端安装/打包入口已删除.

每种便携包包含窗口和命令行入口, 共用 `_internal` 运行库.
Windows 用 exe, Linux 用对应本地可执行文件. 两种系统都保留 GUI 和 CLI Python 源码.
输出统一使用 `--output-dir dist/current`; 不把上一轮 dist 根目录里的旧 ZIP 当作新交付.

## 本机构建 Windows 成品

使用 CPython 3.12, 位数与交付目标一致:
```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\.venv-build\Scripts\python.exe build_exe.py --target windows-sender --arch x64 --output-dir dist/current
.\.venv-build\Scripts\python.exe build_exe.py --target windows-receiver --arch x64 --output-dir dist/current
```

构建 x86 时用 `py -3.12-32` 创建另一个环境, 并传 `--arch x86`.
不能用 64 位 Python 仅修改输出文件名来生成 32 位程序.
打包流程检查固定依赖版本, 不会偷偷自动安装或降级依赖.
源码及依赖版本记录在 BUILD-INFO.json, 每个 ZIP 旁都有 SHA-256.

## 构建 Linux 便携包

须在与目标兼容的 Linux 机器/容器构建, 不能从 Windows 直接交叉打包.
推荐 x64 基线为 Rocky Linux 8 + glibc 2.28 + CPython 3.12, 让较新的 Ubuntu、Debian 和 RHEL 系统更可能共用.
这不是“所有 Linux 通用”; glibc 2.17 的 RHEL/CentOS 7、musl/Alpine、其他 CPU 或禁止执行程序的机器不在此基线内.

在构建机安装 Python 3.12/Tk、图形库和构建工具后:
```bash
python3.12 -m venv .venv-build
.venv-build/bin/python -m pip install -r requirements.txt -r requirements-build.txt
.venv-build/bin/python build_exe.py --target linux-sender --arch x64 --output-dir dist/current
.venv-build/bin/python build_exe.py --target linux-receiver --arch x64 --output-dir dist/current
```

目标机解压便携包即可运行, 不需要安装 Python/Tk/pip, 不需要 sudo.
但需要合适的 glibc、CPU、动态加载器, GUI 还需图形服务和可用系统字体/字体配置; MP4 CLI 可不显示窗口.
x86 32 位 Linux 当前的 Pillow/OpenCV/NumPy 固定版本缺少现成 wheels.
构建器能检查 32 位宿主/输出标签, 但没有提供或验证 Linux x86 二进制.
若必须支持这种旧环境, 需要额外的 32 位依赖源码编译、打包和目标机验证, 不能退回旧库冒充兼容.

## 自动构建工程

`.github/workflows/packages.yml` 配置 Windows x64 双角色和 Rocky Linux 8 x64 双角色.
使用系统自带的 Python/Tk 在构建环境中打包, 并在 Xvfb/Windows 中运行 GUI 和文件内容验收.
输出为 Actions Artifacts, 不自动创建 Releases 或部署.
用户已批准验收后推送并发布. master 更新执行构建, v* 标签在四个目标全部构建/验收成功后,
下载 Actions 附件并核对 12 个 ZIP 的 SHA-256, 然后发布 GitHub Release. Windows x86 暂不进入本轮矩阵.
Release 说明取自 docs/RELEASE_NOTES.md. 不将未运行的目标标成已验证.

## 四种源码和离线库包

任意操作系统都能生成四种源码包:
```bash
python build_exe.py --target windows-sender --source --output-dir dist/current
python build_exe.py --target windows-receiver --source --output-dir dist/current
python build_exe.py --target linux-sender --source --output-dir dist/current
python build_exe.py --target linux-receiver --source --output-dir dist/current
```

离线库必须匹配角色、目标系统、CPU 和 Python. 例如 Linux 接收端 CPython 3.12 / glibc >= 2.28 / x64:
```bash
python download_packages.py --role receiver --platform manylinux_2_28_x86_64 --python-version 3.12
python build_exe.py --target linux-receiver --source --wheel-dir offline_packages/receiver-manylinux_2_28_x86_64-cp312 --output-dir dist/current
```

Windows x64 用 `--platform win_amd64`, x86 用 `win32`; 发送端用 `--role sender` 和对应 target.
Linux ARM 平台参数可用于下载可获得的 wheels, 但不能把 x64 本机生成的可执行文件当作 ARM 成品.
下载器要求空目录, pip 失败不写成功清单. 新格式清单区分收发角色和系统, 不兼容旧的仅发送端清单.
离线安装用 `install.cmd --offline` / `bash install.sh --offline`, 不联网, 不做管理员安装.
wheels 不含系统 Python/Tk/venv; 这些也无法安装时, 应选便携成品.

## 验收

```bash
python -m unittest discover -s tests -v
python tests/smoke_package.py dist/current/QRCodeSender-Windows-x64.zip --role sender
python tests/smoke_package.py dist/current/QRCodeReceiver-Windows-x64.zip --role receiver
```

Linux 改成对应 ZIP, GUI 验收需要图形环境或 Xvfb.
成品验收会解压到新目录、移除 Python 环境变量和开发 PATH, 验证两端 GUI/CLI 启动、
发送端生成码的数据内容及接收端的 MP4 逐字节恢复和失败状态.
`--self-test` 是 GUI 的维护入口; 普通用户直接打开窗口即可.

[验证范围](VALIDATION.md) 区分实构/实测与未运行的目标.
参考: [PyInstaller 的 glibc 限制](https://pyinstaller.org/en/stable/usage.html),
[pip 的跨平台下载参数](https://pip.pypa.io/en/stable/cli/pip_download/).

## 固定的 Linux 构建环境

可复用仓库的 Dockerfile (只在开发构建机准备依赖, 不在用户电脑安装软件):
Dockerfile 的构建上下文需要四份 requirements 文件. 镜像构建后将不含 .git/密钥的源码工作副本挂到 /work;
默认命令执行 packaging/build_linux.sh, 包括测试、双端打包和成品验收. 也可追加 bash packaging/build_linux.sh receiver 只构建接收端.


Linux spec 明确收集 XCB/OpenGL 分派客户端库; 接收端 runtime hook 使 MSS 在冻结目录定位 XCB, 不依赖目标机的 ldconfig 开发库发现.
不携带 glibc、显卡驱动或图形服务器. GUI 在无字体环境中不能工作, 中文字体应由目标桌面提供.
离线包 install.cmd 会选择清单匹配的 Python 版本和 32/64 位, Linux install.sh 默认选择清单对应的 python3.x, 也可通过 PYTHON_BIN 指定解释器.

自动工程参考: [checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python), [upload-artifact](https://github.com/actions/upload-artifact).

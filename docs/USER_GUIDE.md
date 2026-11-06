# 从 Linux 屏幕传文件到另一台电脑

电脑 A (Linux) 显示连续二维码 → 摄像头录成 MP4 → 电脑 B (Windows) 解析视频 → 恢复原文件.
摄像头不用连接到本软件; 先用相机或手机自己的录制功能生成 MP4, 再把视频复制到电脑 B.

## 1. 先选对包

| 使用位置 | 包名 | 解压后用哪个入口 |
|---|---|---|
| 电脑 B, Windows 10/11 x64 | `QRCodeReceiver-Windows-x64.zip` | 双击 `QRCodeReceiver/QRReceiver.exe` |
| 电脑 A, Linux, 可以联网 | `QRCodeSender-Linux-source.zip` | 先 `bash setup_sender.sh`, 再 `bash send.sh 文件路径` |
| 电脑 A, Linux, 不能联网 | `QRCodeSender-manylinux2014_x86_64-cp312-offline.zip` 等带环境标签的包 | 核对 Python/架构后运行 `bash install_offline.sh` |
| 电脑 A, 与构建机兼容的 Linux | `QRCodeSender-Linux-x64.zip` (需在 Linux 构建) | `./QRCodeSender/QRSend 文件路径` |

先使用本次交付的本地 ZIP 包. 项目仓库为 https://github.com/luck-gh/QRCodeTransceiver,
如果发布者尚未提供 Releases 成品, GitHub 的 Code → Download ZIP 只是源码, 不能双击当作 exe 使用.
构建方法见 `docs/BUILDING.md`. 不要把 Windows 包放到 Linux 上运行.

**ZIP 必须先完整解压.** B 端建议解压到 `D:\QRTransfer`, 不要直接在压缩包里双击 exe,
也不要只复制其中一个 exe: 它旁边的 `_internal` 文件夹是程序的一部分.
A 端建议解压到自己的主目录, 例如 `~/QRTransfer`.

## 2. 在电脑 A 准备发送端

以下命令在 Linux 的终端里运行. 把路径替换成自己实际解压的位置.

```bash
cd ~/QRTransfer/QRCodeSender
python3 --version
uname -m
```

源码安装入口支持 CPython 3.10、3.11、3.12. 还需要图形桌面、Tk 和 venv.
Ubuntu/Debian 可以先由管理员准备系统组件:

```bash
sudo apt install python3 python3-venv python3-tk
```

其他 Linux 发行版需要对应的 Python/Tk 组件. 不在无图形桌面的纯 SSH 终端里播放二维码.
`python3 -m tkinter` 能打开一个小窗口, 表示 Tk 和图形显示可用, 确认后关闭即可.

### A 可以联网

```bash
bash setup_sender.sh
```

脚本会在当前包内创建 `venv` 并安装发送端依赖, 不修改系统 Python 的包.
如要使用特定 Python, 可以执行 `PYTHON_BIN=python3.12 bash setup_sender.sh`.

### A 不能联网

Python、venv、Tk、glibc 和图形桌面是**系统组件**, 需要管理员使用该发行版的软件源或离线安装介质准备.
离线包里的 wheel 只负责 Python 库, 不能代替这些系统组件.

例如文件名里的 `x86_64-cp312` 表示 **Linux x86_64 + CPython 3.12**, 不是通用 Linux 包.
`aarch64` 是另一种 CPU, `cp310`/`cp311` 是另一个 Python 版本. 此类包要求 glibc >= 2.17,
不用于 Alpine/musl 系统. 确认环境匹配后执行:

```bash
bash install_offline.sh
```

脚本只从包内 `wheelhouse` 安装, 不联网. 版本、架构、文件摘要或依赖清单不匹配会报错并停止.
遇到不匹配时请让提供包的人按 A 的环境重新下载并打包, 不要手工把旧目录中的 wheels 混进去.

## 3. 在 A 播放二维码

先用一个小文本文件试传:

```bash
bash send.sh "/home/你的用户名/Documents/hello.txt" --chunk-size 256 -i 150 --error-correction Q
```

如果使用 Linux 原生可执行包, 将 `bash send.sh` 换成 `./QRSend`.
窗口会持续更新二维码. 发送端没有接收反馈, 不会自己知道 B 是否已收齐; 请保持播放直到确认录制够长.
通过手机/摄像头拍摄时, 先用上述较慢参数提高识别机会, 传输稳定后再调整速度.

**多个文件先打成一个 zip 再发送.** 当前协议对轮流播放多个文件的流区分不足,
不要直接用通配符轮播多个文件来做本流程的可靠传输.

## 4. 摄像头如何录制

1. 打开相机/手机的录像功能, 将整个二维码和四周白边拍进画面.
2. 对焦到二维码, 让黑白小方块清晰可辨; 减少反光、过曝和运动模糊.
3. 开始录制后保持 A 的二维码持续播放. 可以移动和抖动, 但二维码不能长期出画或被挡住.
4. 优先使用相机可提供的清晰 MP4 录制设置. 先试传小文件; 不要经聊天软件转发压缩视频.
5. 停止录制, 将 MP4 原文件复制到 B, 例如 `D:\QRTransfer\recording.mp4`.

没有固定的“录几秒必定够”: 时长取决于文件大小、可识别的不同二维码数量及拍摄质量.
B 报数据不足时, 需回到 A 重新录一段更长、更清晰的视频. 当前版本不支持把多个 MP4 的未完成状态合并续传.

## 5. 在 B 用窗口恢复文件 (推荐)

1. 完整解压 Windows 接收包, 双击 `QRCodeReceiver/QRReceiver.exe`, 无需安装 Python.
2. 可先点“目录”选一个可写的保存位置. 默认保存到 **QRReceiver.exe 所在文件夹的 decoded 子目录**.
3. 点“视频”, 选择刚复制过来的 MP4. 不用先点“开始”; “开始”是实时截屏接收.
4. 窗口显示当前处理的帧和文件恢复进度. 视频不按播放时间等待, 处理慢也会逐帧往后读.
5. 看到“视频解析完成, 已恢复 … 个文件”后, 到保存目录取文件. 程序会沿用原文件名.

二维码移动后程序会跟随位置; 局部找不到时会搜索当前完整画面, 不要求二维码固定在一个像素位置.
不勾“同名覆写”时, 重名文件会添加后缀, 不会覆盖原来的文件. “停止”可取消视频解析.

## 6. B 也可以用命令行

打开 PowerShell, 进入解压后的 `QRCodeReceiver` 文件夹:

```powershell
.\QRReceiveCLI.exe --video "D:\QRTransfer\recording.mp4" --output-dir "D:\QRTransfer\received"
$LASTEXITCODE
```

命令行必须显式给 `--output-dir` 才会保存到示例位置; 不给时默认是当前工作目录下的 `decoded`.
Linux/Python 开发环境也可以运行 `python qrcode_rx.py --video recording.mp4 --output-dir decoded`.

| 结果 | 含义 | 怎么做 |
|---|---|---|
| 显示解析完成 / 命令行退出码 `0` | 文件恢复完成 | 到选定目录检查文件 |
| 显示“视频结束, 数据不足” / 退出码 `2` | 视频读完了, 编码包还不够 | 重新录制更长、更清晰的视频; 未完成文件不会当作成功文件保存 |
| 显示“视频解析失败” / 退出码 `1` | 文件路径错误、损坏或当前解码库不支持该编码 | 先确认视频能正常播放; 尝试相机的标准 H.264 MP4 录制方式 |
| 用 `--no-save` 退出码 `0` | 只完成编码包提取 | 此模式不代表原文件已经恢复, 普通使用不需要它 |

视频中的帧数不是收到的独立编码包数, 同一个二维码出现很多帧不会增加有效数据量.
视频没拍到的信息、严重模糊的小方块或长期裁切的二维码, 软件无法补回.

# QR Code Transceiver - 二维码喷泉码传输工具

通过屏幕显示和截屏识别 QR 码，实现文件的单向传输.使用喷泉码 (LT 码) 确保可靠传输，无需反馈通道.

## 特性

- **喷泉码传输**: 基于 LT 码 (Luby Transform) ，支持无反馈单向传输
- **自动压缩**: 使用 zlib 压缩，大幅减少传输数据量
- **自动定位**: RX 自动检测屏幕上的二维码位置
- **视频接收**: 直接解析 MP4, 跟随二维码移动, 丢失后自动搜索完整画面
- **实时模式**: TX 默认实时生成新的 droplet，提高传输效率
- **进度显示**: RX 显示实时进度条
- **多线程截屏**: RX 使用独立线程截屏，提高处理速度
- **GUI 接收端**: 提供图形界面版本，可打包为独立 exe
- **离线安装**: 支持在无网络环境下安装部署
- **多文件传输**: 支持文件列表和通配符批量传输

## 从哪里开始

使用流程: Linux 电脑 A 播放二维码 → 摄像头录成 MP4 → Windows 电脑 B 解析 → 恢复文件.

**第一次使用请看 [新手操作说明](docs/USER_GUIDE.md)**, 包含选包、解压、A 端安装、摄像头录制、B 端解析和保存位置.

| 交付物 | 用途 |
|---|---|
| `QRCodeReceiver-Windows-x64.zip` | B 端完整解压后双击 `QRReceiver.exe`, 无需安装 Python; 命令行使用 `QRReceiveCLI.exe` |
| `QRCodeSender-Linux-source.zip` | A 端有网时运行 `bash setup_sender.sh`, 再用 `bash send.sh 文件路径` |
| `QRCodeSender-manylinux2014_x86_64-cp312-offline.zip` 等 | A 端的特定 Python/架构离线包, 核对标签后运行 `bash install_offline.sh` |

这些名称是构建输出. 仓库源码 ZIP 不包含 exe, 当前没有已发布的 GitHub Releases 成品.
本次功能和打包工程位于 [wip/qr-transfer-rework-20260825 分支](https://github.com/luck-gh/QRCodeTransceiver/tree/wip/qr-transfer-rework-20260825), 下载源码时请选择该分支.
[构建与离线分发说明](docs/BUILDING.md) 提供 Windows 接收端、Linux 发送端以及离线包的可重复构建命令.
[本次验证记录](docs/VALIDATION.md) 区分已运行验证和需要目标环境验收的部分.
离线 wheels 不包括系统 Python、Tk、venv 和图形桌面, 也不是跨 Python/CPU 通用包.

开发环境安装全部依赖: `python -m pip install -r requirements.txt`;
只发送或接收时分别使用 `requirements-tx.txt` / `requirements-rx.txt`.

## 使用方法

### 发送端 (TX)

```bash
python qrcode_tx.py <文件路径> [选项]
```

Linux 下也可以直接用仓库里的 `qrtx`:

```bash
./qrtx myfile.zip
```

如果仓库不在当前目录，可先用 `setenv QRTX_ROOT /path/to/qrcode_transceiver` 指向项目根目录.

#### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `filename` | 需要传输的文件路径，支持通配符 | (与 -f 二选一) |
| `-f, --file-list` | 文件列表文件，每行一个文件路径 | - |
| `-o, --output` | 二维码图片输出目录 | `droplets` |
| `--chunk-size` | 每个分块的字节数 | `512` |
| `--extra` | 额外生成 droplet 的比例 | `0.5` (50%) |
| `--size` | 二维码图片边长 (像素)  | `800` |
| `--border` | 二维码静区宽度 (模块数)  | `4` |
| `-i, --interval` | 播放间隔 (毫秒)  | `30` |
| `--error-correction` | QR 纠错等级，L/M/Q/H，等级越高越稳但容量越低 | `M` |
| `--no-display` | 仅生成图片，不弹出播放窗口 | - |
| `--no-live` | 禁用实时模式，使用预生成模式 | - |
| `--no-compress` | 禁用自动压缩 | - |

#### 示例

```bash
# 基本用法 (默认实时模式)
python qrcode_tx.py myfile.zip

# 预生成模式
python qrcode_tx.py bigfile.exe --no-live

# 自定义参数
python qrcode_tx.py myfile.zip --size 700 -i 100

# 激进吞吐参数，只有在屏幕清晰、RX 识别稳定时使用
python qrcode_tx.py myfile.zip --chunk-size 1536 -i 20 --error-correction L

# 禁用压缩
python qrcode_tx.py already_compressed.zip --no-compress

# 多文件: 使用通配符 (注意: 通配符必须加引号，否则会被 shell 展开)
python qrcode_tx.py "*.txt"

# 多文件: 使用文件列表
python qrcode_tx.py -f files.list
```

#### 文件列表格式

文件列表 (`files.list`) 每行一个文件路径，支持 `#` 开头的注释:

```
# 要传输的文件列表
document.pdf
data/config.json
logs/app.log
```

### 接收端 (RX)

#### 命令行版本

```bash
python qrcode_rx.py [选项]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--video` | 从 MP4 等视频文件接收; 不指定则截屏接收 | - |
| `--monitor` | mss 的 monitor 索引 | `1` (主屏) |
| `--region` | 指定截屏区域 (LEFT TOP WIDTH HEIGHT) | 自动检测 |
| `--no-auto-region` | 禁用自动定位二维码区域 | - |
| `-i, --interval` | 截屏间隔 (秒)  | `0.02` |
| `--dedup` | 按 seed 去重，避免重复处理 | - |
| `--quiet` | 静默模式，减少输出 | - |
| `--debug` | 调试模式，输出详细信息 | - |
| `--output-dir` | 接收文件保存目录 | `decoded` |
| `--no-save` | 仅输出 droplet，不保存文件 | - |

#### 从 MP4 视频接收

```bash
python qrcode_rx.py --video recording.mp4 --output-dir decoded
```

视频按顺序逐帧处理, 不按播放速度等待, 不因为处理速度慢而跳帧. 找到二维码后优先识别附近区域,
每次识别成功都更新位置; 局部识别失败时立即搜索当前完整帧, 不清空已经收到的数据.
允许二维码平移、抖动及解码器可识别范围内的旋转和透视变化. 同一个编码包只处理一次.

全部声明文件恢复后自动结束. 若视频读完仍未收齐, 命令行返回退出码 `2`, 提示当前恢复进度;
文件打不开或读不到画面时返回 `1`. `--region` 和 `--no-auto-region` 只用于屏幕接收,
不能与 `--video` 同时使用. `--interval` 不影响视频解析速度.

录制时应让二维码完整入镜、清晰可辨, 并持续录到接收所需的编码包数量. 严重运动模糊、
二维码裁切或录制漏掉太多编码包时, 无法保证恢复成功.

#### GUI 版本

```bash
python qrcode_rx_gui.py
```

或直接运行打包好的 `QRReceiver.exe`.

GUI 界面功能:
- **▶ 开始** - 开始接收
- **⏹ 停止** - 停止接收
- **🔄 重置** - 清除当前接收状态，准备接收新文件
- **📁 目录** - 选择接收文件保存目录
- **视频** - 选择 MP4 文件并开始解析, 显示帧进度, 完成或数据不足时自动结束; “停止”可取消解析
- **同名覆写** - 收到同名文件时直接覆盖已有文件
- **小文本复制** - 单个 `.txt` 文件不超过 5KB 时，保存后自动复制内容到剪贴板

### 构建交付物

在 Windows 构建接收端:

```powershell
python -m pip install -r requirements-rx.txt -r requirements-build.txt
python build_exe.py --target windows-receiver
```

在任意系统生成 Linux 发送源码包:

```bash
python build_exe.py --target linux-sender-source
```

Linux 原生可执行包使用 `--target linux-sender`, 必须在 Linux 构建.
详细环境和验证要求见 [BUILDING.md](docs/BUILDING.md).

## 文件说明

| 文件 | 说明 |
|------|------|
| `qrcode_tx.py` | 发送端程序 |
| `qrcode_rx.py` | 接收端程序 (命令行)  |
| `qrcode_rx_gui.py` | 接收端程序 (GUI)  |
| `qrcode_video.py` | 视频逐帧读取和移动二维码识别 |
| `fountain.py` | 喷泉码核心实现 |
| `build_exe.py` | exe 打包脚本 |
| `download_packages.py` | 离线包下载脚本 |
| `install_offline.sh` | 离线安装脚本 (Linux)  |
| `requirements.txt` | 依赖列表 |

## 原理说明

### 喷泉码 (LT 码)

喷泉码是一种无速率纠删码，具有以下特点:

- **无反馈**: 发送端不需要知道接收端的状态
- **无限生成**: 可以生成任意数量的编码包
- **冗余容错**: 接收端只需要收到略多于原始分块数的包即可恢复数据

### 鲁棒孤波分布

本项目使用鲁棒孤波分布 (Robust Soliton Distribution) 来选择每个 droplet 包含的源块数量，这是 LT 码的关键组成部分，能够在保证译码成功率的同时最小化所需的 droplet 数量.

## 注意事项

1. **QR 码大小**: `--size` 参数影响 QR 码的可识别性.太小可能导致识别率下降.
2. **播放间隔**: `-i` 参数需要根据 RX 的处理能力调整.间隔太短可能导致丢包.
3. **压缩效果**: 对于已压缩的文件 (如 .zip, .jpg) ，压缩可能无效，会自动跳过.
4. **大文件传输**: 默认优先保证识别稳定.若 RX 长时间停在“等待检测二维码”，优先降低 `--chunk-size`，例如 `--chunk-size 512`; 识别稳定后再尝试减小 `-i` 或降低纠错等级.

## License

MIT

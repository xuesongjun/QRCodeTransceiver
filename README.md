# QR Code Transceiver - 二维码喷泉码传输工具

通过屏幕显示和截屏识别 QR 码，实现文件的单向传输。使用喷泉码（LT 码）确保可靠传输，无需反馈通道。

## 特性

- **喷泉码传输**：基于 LT 码（Luby Transform），支持无反馈单向传输
- **自动压缩**：使用 zlib 压缩，大幅减少传输数据量
- **自动定位**：RX 自动检测屏幕上的二维码位置
- **实时模式**：TX 可实时生成新的 droplet，提高传输效率
- **进度显示**：RX 显示实时进度条
- **多线程截屏**：RX 使用独立线程截屏，提高处理速度
- **离线安装**：支持在无网络环境下安装部署

## 快速安装

### 在线安装（有网络）

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Linux/Mac:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 离线安装（无网络的 Linux 机器）

#### 步骤 1：在有网络的 Windows 机器上下载依赖包

```bash
python download_packages.py
```

按提示选择目标 Linux 平台和 Python 版本。

#### 步骤 2：复制文件到目标机器

将以下文件/目录复制到目标 Linux 机器：

```
qrcode_transceiver/
├── offline_packages/     # 离线包目录
├── install_offline.sh    # 安装脚本
├── requirements.txt
├── qrcode_tx.py
├── qrcode_rx.py
├── FF.py
└── ...
```

#### 步骤 3：在目标机器上安装

```bash
# 添加执行权限
chmod +x install_offline.sh

# 运行安装脚本
./install_offline.sh
```

#### 步骤 4：使用

```bash
# 激活虚拟环境
source venv/bin/activate

# 运行程序
python qrcode_rx.py
```

## 虚拟环境说明

### 创建虚拟环境

```bash
# Linux/Mac
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### 退出虚拟环境

```bash
deactivate
```

### 删除虚拟环境

```bash
# 直接删除 venv 目录即可
rm -rf venv  # Linux/Mac
rmdir /s venv  # Windows
```

## 使用方法

### 发送端 (TX)

```bash
python qrcode_tx.py <文件路径> [选项]
```

#### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `filename` | 需要传输的文件路径 | (必需) |
| `-o, --output` | 二维码图片输出目录 | `droplets` |
| `--chunk-size` | 每个分块的字节数 | `512` |
| `--extra` | 额外生成 droplet 的比例 | `0.5` (50%) |
| `--size` | 二维码图片边长（像素） | `800` |
| `--border` | 二维码静区宽度（模块数） | `4` |
| `-i, --interval` | 播放间隔（毫秒） | `50` |
| `--no-display` | 仅生成图片，不弹出播放窗口 | - |
| `--no-live` | 禁用实时模式，使用预生成模式 | - |
| `--no-compress` | 禁用自动压缩 | - |

#### 示例

```bash
# 基本用法（默认实时模式）
python qrcode_tx.py myfile.zip

# 预生成模式
python qrcode_tx.py bigfile.exe --no-live

# 自定义参数
python qrcode_tx.py myfile.zip --size 700 -i 100

# 禁用压缩
python qrcode_tx.py already_compressed.zip --no-compress
```

### 接收端 (RX)

```bash
python qrcode_rx.py [选项]
```

#### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--monitor` | mss 的 monitor 索引 | `1` (主屏) |
| `--region` | 指定截屏区域 (LEFT TOP WIDTH HEIGHT) | 自动检测 |
| `--no-auto-region` | 禁用自动定位二维码区域 | - |
| `-i, --interval` | 截屏间隔（秒） | `0.05` |
| `--dedup` | 按 seed 去重，避免重复处理 | - |
| `--quiet` | 静默模式，减少输出 | - |
| `--debug` | 调试模式，输出详细信息 | - |
| `--output-dir` | 接收文件保存目录 | `decoded` |
| `--no-save` | 仅输出 droplet，不保存文件 | - |

#### 示例

```bash
# 基本用法（自动检测二维码位置）
python qrcode_rx.py

# 指定截屏区域
python qrcode_rx.py --region 100 100 600 600

# 调整截屏间隔
python qrcode_rx.py -i 0.03

# 调试模式
python qrcode_rx.py --debug
```

## 传输流程

1. **TX 端**：
   - 读取文件并自动压缩
   - 将数据分割为多个 chunk
   - 使用喷泉码生成 droplet
   - 将 droplet 编码为 QR 码并循环显示

2. **RX 端**：
   - 截取屏幕并识别 QR 码
   - 解码 droplet 并进行喷泉码译码
   - 收集足够的 droplet 后恢复原始数据
   - 自动解压并保存文件

## 性能优化建议

### 小文件 (< 1MB)
```bash
# TX
python qrcode_tx.py small.txt --size 500 -i 100

# RX
python qrcode_rx.py -i 0.03
```

### 中等文件 (1-10MB)
```bash
# TX
python qrcode_tx.py medium.zip --size 600 --live -i 120

# RX
python qrcode_rx.py -i 0.05
```

### 大文件 (> 10MB)
```bash
# TX
python qrcode_tx.py large.exe --size 700 --live -i 150

# RX
python qrcode_rx.py -i 0.05
```

## 原理说明

### 喷泉码 (LT 码)

喷泉码是一种无速率纠删码，具有以下特点：

- **无反馈**：发送端不需要知道接收端的状态
- **无限生成**：可以生成任意数量的编码包
- **冗余容错**：接收端只需要收到略多于原始分块数的包即可恢复数据

### 鲁棒孤波分布

本项目使用鲁棒孤波分布 (Robust Soliton Distribution) 来选择每个 droplet 包含的源块数量，这是 LT 码的关键组成部分，能够在保证译码成功率的同时最小化所需的 droplet 数量。

## 文件说明

| 文件 | 说明 |
|------|------|
| `qrcode_tx.py` | 发送端程序 |
| `qrcode_rx.py` | 接收端程序 |
| `fountain.py` | 喷泉码核心实现 |
| `server.py` | 摄像头接收端（备用） |
| `download_packages.py` | 离线包下载脚本（Windows） |
| `install_offline.sh` | 离线安装脚本（Linux） |
| `requirements.txt` | 依赖列表 |

## 注意事项

1. **QR 码大小**：`--size` 参数影响 QR 码的可识别性。太小可能导致识别率下降。
2. **播放间隔**：`-i` 参数需要根据 RX 的处理能力调整。间隔太短可能导致丢包。
3. **压缩效果**：对于已压缩的文件（如 .zip, .jpg），压缩可能无效，会自动跳过。
4. **大文件传输**：建议使用 `--live` 模式，并适当增大 `--size` 和 `-i` 参数。

## License

MIT

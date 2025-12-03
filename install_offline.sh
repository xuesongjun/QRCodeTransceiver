#!/bin/bash
# install_offline.sh - 离线安装 Python 依赖包
# 用法：在没有网络的目标机器上运行此脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGES_DIR="${SCRIPT_DIR}/offline_packages"
VENV_DIR="${SCRIPT_DIR}/venv"

echo "=========================================="
echo "QR Code Transceiver 离线安装"
echo "=========================================="

# 检查 Python 版本
PYTHON_CMD=""
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "错误：找不到 Python，请先安装 Python 3.8+"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
echo "检测到 Python 版本: $PYTHON_VERSION"

# 检查离线包目录
if [ ! -d "$PACKAGES_DIR" ]; then
    echo "错误：找不到离线包目录 $PACKAGES_DIR"
    echo "请先在有网络的机器上运行 download_packages.sh"
    exit 1
fi

# 创建虚拟环境
echo ""
echo "步骤 1/3: 创建虚拟环境..."
if [ -d "$VENV_DIR" ]; then
    read -p "虚拟环境已存在，是否删除重建？[y/N]: " REBUILD
    if [ "$REBUILD" = "y" ] || [ "$REBUILD" = "Y" ]; then
        rm -rf "$VENV_DIR"
        $PYTHON_CMD -m venv "$VENV_DIR"
    fi
else
    $PYTHON_CMD -m venv "$VENV_DIR"
fi

# 激活虚拟环境
echo ""
echo "步骤 2/3: 激活虚拟环境..."
source "$VENV_DIR/bin/activate"

# 升级 pip（使用离线包中的 pip，如果有的话）
if ls "$PACKAGES_DIR"/pip-*.whl 1> /dev/null 2>&1; then
    pip install --no-index --find-links="$PACKAGES_DIR" pip --upgrade 2>/dev/null || true
fi

# 安装依赖
echo ""
echo "步骤 3/3: 安装依赖包..."
pip install --no-index --find-links="$PACKAGES_DIR" \
    opencv-python \
    numpy \
    mss \
    qrcode \
    Pillow

echo ""
echo "=========================================="
echo "安装完成！"
echo "=========================================="
echo ""
echo "使用方法："
echo ""
echo "1. 激活虚拟环境："
echo "   source ${VENV_DIR}/bin/activate"
echo ""
echo "2. 运行接收端："
echo "   python qrcode_rx.py"
echo ""
echo "3. 运行发送端："
echo "   python qrcode_tx.py <文件路径>"
echo ""
echo "4. 退出虚拟环境："
echo "   deactivate"

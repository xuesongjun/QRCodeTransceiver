# 本次验证记录

日期: 2026-11-06. 构建系统: Windows 11 x64, CPython 3.12.4.
构建工具和运行依赖使用 `requirements-build.txt`、`requirements-rx.txt` 中记录的版本.

## 已验证

- 21 项自动回归通过: MP4 移动、位置跳变、轻微旋转、短暂消失、重复画面/编码包、恢复内容及错误分支;
  离线清单的摘要、环境限制、旧文件混入和源码 ZIP 内容.
- 实际生成 `QRCodeReceiver-Windows-x64.zip`, 解压到新的临时目录.
- 成品子进程移除 Python 环境变量, PATH 仅保留 Windows 系统目录:
  GUI 创建 Tk 窗口控件后自检退出; 命令行 `--help` 包含 `--video`;
  从压缩 MP4 恢复的文件与原始 700 字节随机数据逐字节一致;
  数据不足返回 `2` 且不写出未完成文件, 不存在的视频返回 `1`.
- 生成 Linux 发送源码 ZIP, 核对发送入口、固定依赖、构建脚本、说明、脚本 LF 换行及可执行权限.
- 下载并校验 CPython 3.12 + manylinux2014 x86_64 的实际 wheels, 生成带目标标签的离线 ZIP.
  该离线包不适用于其他 Python 版本、CPU 架构或 musl 系统.
- 所有交付 ZIP 旁有 SHA-256 文件, 包内有源码摘要及实际打包环境.

## 验证范围

- 当前没有可用的 Linux 运行环境, 没有在 Linux 上实际安装离线包、播放 Tk 窗口或构建原生 Linux 成品.
  Linux 构建和安装入口已经提供, 需要在对应目标环境验收; 本机 Bash 检查也受宿主启动限制, 不计为通过.
- Windows 成品在当前 Windows 主机运行通过, 尚未在完全没有开发工具的干净 Windows 机器上验证.
- 视频样本由测试生成, 包含 MP4 压缩和二维码运动; 尚未用用户实际摄像头录制的视频测量成功率.

复验命令见 [BUILDING.md](BUILDING.md). 普通用户操作见 [USER_GUIDE.md](USER_GUIDE.md).

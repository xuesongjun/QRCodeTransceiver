# QRCodeTransceiver

通过动态二维码传文件, 支持 Windows/Linux 双端窗口、命令行、MP4 逐帧接收、保存续收和定向补传.
远端仓库: https://github.com/luck-gh/QRCodeTransceiver

新传输协议 QT2 每包绑定具体任务、分块参数和校验. 每轮先覆盖全部源块, 再发新的 LT 喷泉修复包.
漏包不导致后续编码包失效; 译码器先剥皮, 再用 GF(2) 高斯消元求解残余方程. 只有完整编码数据和最终文件 SHA-256 校验都通过才标记完成.

## 新手入口

完整流程见 [使用说明](docs/USER_GUIDE.md). 四种角色的安装、构建见 [构建说明](docs/BUILDING.md).
[Release 下载](https://github.com/luck-gh/QRCodeTransceiver/releases) 提供正式附件, 本地构建在 `dist/current`, 实际验收范围见 [验证记录](docs/VALIDATION.md).

| 角色 | Windows 便携入口 | Linux 便携入口 | Python 窗口 / 命令行 |
|---|---|---|---|
| 发送 | QRSend.exe / QRSendCLI.exe | QRSend / QRSendCLI | qrcode_tx_gui.py / qrcode_tx.py |
| 接收 | QRReceiver.exe / QRReceiveCLI.exe | QRReceiver / QRReceiveCLI | qrcode_rx_gui.py / qrcode_rx.py |

两端都有小窗口; 便携包请完整解压, 保留 `_internal`. 源码包保留脚本方式, 离线源码包附带匹配的 Python wheels.
Windows x64 和 Linux x64 成品的验证情况以验证记录为准. Windows x86 仅提供构建工程和离线库包; Linux x86 缺少当前固定依赖的预编译库, 暂无成品.

## 使用

1. 发送端新建任务并保存 `.qrtx`, 开始发送; 每轮进度条有数字和颜色, 能看出开头、末尾及循环.
2. 接收端导入 MP4, 或录制屏幕、停止并保存后解析. 二维码可移动, 局部定位失败会搜索同一完整帧.
3. 不完整时自动保存 `.qrrx`. 下次选择“继续原任务”, 或继续追加视频; 重复视频/包不增加恢复进度.
4. 生成单行补传码, 复制/导出到发送端, 打开原 `.qrtx` 校验后只补发所需源块.
5. 接收端追加补传视频, 完成校验并保存文件. 可多轮补传; “新建/重置”与“继续原任务”明确分开.

```bash
python qrcode_tx_gui.py
python qrcode_rx_gui.py
# 命令行创建并冻结任务
python qrcode_tx.py myfile.zip --task myfile.qrtx
# 接收后即使不完整也保存现场; 不完整退出码为 2
python qrcode_rx.py --video first.mp4 --task myfile.qrrx --request-out repair.qrreq
python qrcode_tx.py --task myfile.qrtx --repair repair.qrreq
python qrcode_rx.py --video repair.mp4 --task myfile.qrrx
# 旧格式视频的独立一次性入口
python qrcode_rx.py --legacy --video old.mp4
```

开发依赖: `python -m pip install -r requirements.txt`; 测试: `python -m unittest discover -s tests -v`.
新发送端统一为单文件任务, 多文件先打成 ZIP; 删除旧的多文件交织/预生成循环契约. 用户要求的旧 MP4 一次性解析入口保留, 不合并新任务.
[协议说明及后续节奏同步讨论](docs/PROTOCOL_NOTES.md) 区分已实现内容与研究方向.
正式交付优先 Windows x64 / Linux x64. 推送 v* 标签后, CI 完成双端构建、验收和源码/离线包准备, 再发布 [GitHub Release](https://github.com/luck-gh/QRCodeTransceiver/releases).

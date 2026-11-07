# 协议与设计记录

## 已实现的 QT2

采用系统编码的组织模式: 普通轮次先发全部源块, 然后发 ceil(K*extra) 个新的 LT 编码包.
源块可直接恢复, LT 包按 seed 从同一份源数据独立生成, 不依赖上一包.
单个包出错不会使后续包异常; 错包会在入口逐包 CRC 校验时拒绝. 当前 LT 算法继续复用 fountain.py, **不是 Raptor/RaptorQ 的实现**.
系统编码的组织参考 [RFC 5053](https://www.rfc-editor.org/rfc/rfc5053), 仅借鉴源符号与修复符号的职责划分.

任务 ID = SHA-256(`QRCodeTransceiver-v2\0` + 大端 u32 块大小 + 冻结编码字节).
冻结编码字节 = 大端 u32 清单长度 + UTF-8 规范 JSON 清单 + 原始/一次 zlib 压缩正文.
清单含 version=2、filename、size、sha256、compression、chunk_size. 任务源块包括清单, 不等同于直接按原文件字节计块.
发送任务 `.qrtx` 保存这一精确数据, 补传不能重新压缩、读取后来修改的文件或沿用只有同名/同块数的数据.

二维码文本为 `QT2:` + URL-safe Base64, 二进制依次为:

| 字段 | 表示 |
|---|---|
| kind | u8: 0=源块, 1=LT 包 |
| task_id | 32 字节 SHA-256 |
| chunk_size, K | 各大端 u32 |
| payload_size | 大端 u64, 用于计算 padding |
| number | 大端 u32: 源块编号或 LT seed |
| round_no | 大端 u32, 从 1 开始 |
| data | 固定 chunk_size 字节 |
| CRC32 | 大端 u32, 覆盖以上全部二进制 |

源块编号从 0 开始, 最后源块补零; padding=0 时不截去数据.
逐包去重按 kind+number+data 摘要, 不包含轮号. 消元循环到无新单块关系可恢复; 先验证整次消元的一致性再提交, 拒绝包不能污染原状态.
恢复整个编码数据后核对任务 ID, 解压后再核对原始长度和 SHA-256. CRC 用于发现传输损坏, 不用于身份认证.

## 现场

`.qrtx`/`.qrrx` 使用 `QRTASK1\0` + 压缩体 SHA-256 + zlib(JSON), JSON 中有版本和角色.
接收现场保存参数、已恢复源块、未解关系、seed/包去重摘要、视频摘要、日志和输出路径.
采用 [Python os.replace](https://docs.python.org/3/library/os.html#os.replace) 的同目录原子替换模式: 写临时文件、flush/fsync、替换成功后才更新保存状态.
不承诺跨文件系统事务或断电情况下目录元数据的完整持久性. 不新增数据库/迁移层.

## QRF1 补传码

`QRF1:` 为版本; 后面 URL-safe Base64 编码二进制封装.
封装为压缩标记 u8 (0=直接,1=zlib) + 内容 + 大端 CRC32, CRC 覆盖标记与存储内容.
内容: 方向 u8 (0=missing,1=success,2=all), 编码 u8 (0=delta,1=ranges,2=bitmap), 32 字节任务标识, u32 总源块数, 选区, 集合数据.
整数均大端 u32; 列表以元素数量 u32 起头. 选区列表为 start,count 对, 空列表表示全部范围.

- delta: 升序源块编号的相邻差值, 第一项相对 0, 后续差值必须大于 0.
- ranges: 不重叠的 start,count 对, 区间为 [start,start+count).
- bitmap: 按选区源块升序, 字节内低位在前, **1=待补传,0=本次不补传**, 末字节空位须为 0. 只与 missing 方向组合.
- missing 列出待补传块; success 列出选区内已恢复块, 请求其选区补集; all 请求全部源块, 不带局部选区.

自动模式实际生成 missing-delta/ranges/bitmap、success-delta/ranges 的完整码, 每种再比较直接/zlib 封装, 挑文本最短者.
集合方向不固定按 50% 判断. 全部重发只由用户明确选择, 不加入自动候选. 局部选区也只请求精确的未恢复源块.
复用 [Python zlib](https://docs.python.org/3/library/zlib.html)、struct 和 base64, 不臆造“MD3/UU”算法, 不把 Base64 膨胀当作压缩收益.

## 录制与识别

MSS 采集与 MP4 写入循环中不运行 QR 识别. 保存视频后统一进入 VideoScanner + MovingQRScanner + ReceiveTask 路径.
逐帧顺序处理, 完全相同的灰度画面可跳过, 完成后可提前停止. ROI 失败会搜索同一完整帧.
无法录到、严重模糊或编码器损坏的画面不能凭回退恢复. 旧视频只在明确的一次性入口中解析, 不推断新任务身份.

## 尚未实现的节奏参考信号/二次识别

用户提出用几个参考信号估计频率, 兼容 0.5x/1x/2x 播放速度. 本轮不实现此采样同步协议.
显示目标间隔不等于真实刷新率; 即便有参考序号/时间差, 仍须估计相位、漂移和重新捕获.
不能按名义 fps 固定丢弃若干帧就宣称同步. 当前逐帧离线解析不依赖播放倍率.

本轮已实现编号/颜色不同的完整轮次进度条, 但没有周期参考信号或按频率降采样.
下一步可研究对失败区间采用不同尺度/透视/锐化的有界二次识别. 同参数重读同帧通常不增加信息.
需有真实摄像头/播放器样本比较恢复率、独立有效包数、QR 调用次数和耗时, 不能仅凭理论宣称吞吐提升.

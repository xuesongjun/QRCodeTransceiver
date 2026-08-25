"""
fountain.py — 喷泉码 (LT 码) 核心实现

原理概述
--------
LT 码 (Luby Transform Code) 是一种无速率纠删码:
  - 发送端 (Fountain) 把原始数据切成 K 个 chunk，
    按鲁棒孤波分布随机选若干 chunk 做 XOR，产生
    一个 droplet (编码包) ，可无限生成.
  - 接收端 (Glass) 收到约 K*(1+ε) 个 droplet 后，
    用置信传播 (BP) 译码恢复所有 K 个 chunk.

数据格式 (droplet 字符串)
--------------------------
  seed|num_chunks|padding|base64_data
  例: 42|10|3|SGVsbG8=

用法示例
--------
  # 发送端
  from fountain import Fountain
  f = Fountain(b"hello world", chunk_size=4)
  droplet_str = f.droplet().getStr()   # 生成一个编码包字符串

  # 接收端
  from fountain import Glass, str2Droplet
  g = None
  while not g or not g.isDone():
      d = str2Droplet(droplet_str)      # 解析编码包
      if g is None:
          g = Glass(d)
      else:
          g.addDroplet(d)
  data = g.getData()                   # 恢复原始数据
"""

from math import ceil
import math
import base64


# ---------------------------------------------------------------------------
# 随机数生成器
# ---------------------------------------------------------------------------

def seeded_random(seed):
    """
    线性同余伪随机数生成器 (LCG) ，用于可复现的随机序列.

    返回一个闭包函数 rd(min, max)，每次调用产生 [min, max] 范围内的整数.
    相同 seed 产生相同序列，保证 TX/RX 两端对同一 seed 选出相同的 chunk 集合.

    参数
    ----
    seed : int 或 None
        初始种子，None 时默认使用 1337.
    """
    l_seed = seed if seed else 1337

    def rd(min=0, max=2147483647):
        nonlocal l_seed
        # sin 扰动 + 小数部分放大，产生伪随机整数
        random = math.sin(l_seed) * 100
        seed_decimal = round(random - math.floor(random), 8)
        l_seed = (math.floor(seed_decimal * 25214903917) + 11) % 2147483647
        return math.floor(seed_decimal * (max - min + 1) + min)

    return rd


# ---------------------------------------------------------------------------
# 度分布: 鲁棒孤波分布
# ---------------------------------------------------------------------------

def robust_solition(K):
    """
    计算 K 个源块对应的鲁棒孤波分布 (累积概率向量) .

    鲁棒孤波分布 = 理想孤波分布 + 鲁棒修正项，归一化后截断低概率尾部.
    返回的累积概率向量用于在随机数 d ∈ [0,1) 上做二分查找，
    得到当前 droplet 应 XOR 的 chunk 数量 (度数) .

    参数
    ----
    K : int
        源块总数 (num_chunks) .

    返回
    ----
    list[float]
        长度为 max_num 的累积概率列表，最后一个元素 ≈ 1.0.
    """
    # 理想孤波分布
    p_ideal = [0] * K
    p_ideal[0] = 1 / K
    for i in range(1, K):
        p_ideal[i] = 1 / (i * (i + 1))

    # 鲁棒孤波分布参数
    c = 0.05      # 控制修正项幅度
    delta = 0.05  # 译码失败概率上界 (即成功率 ≥ 1-delta)
    p_robust = p_ideal.copy()

    R = c * math.log(K / delta) * math.sqrt(K)
    degree_max = min(max(round(K / R), 2), K)  # 度数上限

    p = [0] * degree_max  # 鲁棒修正项
    for i in range(degree_max - 1):
        p[i] = R / ((i + 1) * K)
    p[degree_max - 1] = R * math.log(R / delta) / K

    # 叠加并归一化
    for i in range(degree_max):
        p_robust[i] += p[i]
    sum_p_robust = sum(p_robust)
    p_robust = [x / sum_p_robust for x in p_robust]

    # 截断: 去掉概率极小的尾部 (< 0.1/K)
    max_num = next(i for i in range(len(p_robust) - 1, -1, -1) if p_robust[i] > (0.1 / K)) + 1

    # 转换为累积概率
    distribution_matrix_prob = p_robust[:max_num]
    temp_sum = sum(distribution_matrix_prob)
    distribution_matrix_prob = [x * (1 / temp_sum) for x in distribution_matrix_prob]
    temp_sum = 0
    for i in range(max_num):
        temp_sum += distribution_matrix_prob[i]
        distribution_matrix_prob[i] = round(temp_sum, 8)

    return distribution_matrix_prob


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def randChunkNums(k, prob, seed):
    """
    根据 seed 和度分布 prob，随机选出当前 droplet 需要 XOR 的 chunk 编号集合.

    参数
    ----
    k    : int        源块总数
    prob : list[float] 累积概率向量 (来自 robust_solition)
    seed : int        随机种子

    返回
    ----
    list[int]  已排序的 chunk 编号列表，长度 = 本次选出的度数 d
    """
    r = seeded_random(seed)
    # 在累积概率向量上做二分查找得到度数 d
    d = round(r(0, 2147483646) / 2147483647, 8)
    d = next(i for i in range(len(prob)) if prob[i] >= d) + 1
    k -= 1
    res = set()
    while len(res) < d:
        res.add(r(0, k))
    res = list(res)
    res.sort()
    return res


def xor(str1, str2):
    """对两段等长字节串做逐字节 XOR，返回新字节串."""
    return bytes(a ^ b for a, b in zip(str1, str2))


# ---------------------------------------------------------------------------
# Droplet (编码包)
# ---------------------------------------------------------------------------

class Droplet:
    """
    一个编码包，携带若干 chunk XOR 后的数据及元信息.

    属性
    ----
    data       : bytes   XOR 后的载荷数据
    seed       : int     生成本包的随机种子 (唯一标识)
    num_chunks : int     原始数据的分块总数
    padding    : int     最后一块填充的零字节数
    prob       : list    度分布 (由 Glass 注入，Fountain 端不使用)
    """

    def __init__(self, data, seed, num_chunks, padding):
        self.data = data
        self.seed = seed
        self.num_chunks = num_chunks
        self.prob = None   # 由 Glass 在添加 droplet 时赋值
        self.padding = padding

    def chunkNums(self):
        """根据 seed 和 prob 还原本包所涵盖的 chunk 编号列表."""
        return randChunkNums(self.num_chunks, self.prob, self.seed)

    def getStr(self):
        """
        序列化为可写入 QR 码的字符串.

        格式:   seed|num_chunks|padding|base64_data
        示例:   42|10|3|SGVsbG8=
        """
        data = base64.b64encode(self.data).decode()
        return f'{self.seed}|{self.num_chunks}|{self.padding}|{data}'


def str2Droplet(s: str):
    """
    将 getStr() 序列化的字符串反序列化为 Droplet 对象.

    参数
    ----
    s : str   格式为 "seed|num_chunks|padding|base64_data"

    返回
    ----
    Droplet
    """
    args = s.split('|', maxsplit=3)
    seed = int(args[0])
    num_chunks = int(args[1])
    padding = int(args[2])
    data = base64.b64decode(args[3])
    return Droplet(data, seed, num_chunks, padding)


# ---------------------------------------------------------------------------
# Fountain (发送端)
# ---------------------------------------------------------------------------

class Fountain:
    """
    喷泉码发送端: 将原始数据切块，按需生成编码包 (droplet) .

    用法
    ----
    f = Fountain(data, chunk_size=1024)
    while True:
        droplet_str = f.droplet().getStr()
        # 将 droplet_str 写入 QR 码并展示

    参数
    ----
    data       : bytes  原始数据 (含 header)
    chunk_size : int    每块字节数，影响 QR 码容量与分块数
    seed       : int    初始随机种子，None 时自动生成
    """

    def __init__(self, data: bytes, chunk_size=32, seed=None):
        self.data = data
        self.chunk_size = chunk_size
        self.num_chunks = int(ceil(len(data) / float(chunk_size)))
        self.seed = seed
        self.r = seeded_random(seed)
        self.prob = robust_solition(self.num_chunks)
        # 末尾补零使数据对齐到 chunk_size 整数倍
        self.padding = self.num_chunks * self.chunk_size - len(self.data)
        self.data += b'\00' * self.padding

    def droplet(self):
        """
        生成下一个编码包.

        每次调用先推进随机种子，然后随机选若干 chunk 做 XOR，
        返回包含 XOR 结果和元信息的 Droplet 对象.
        """
        self.updateSeed()
        if self.num_chunks > 1:
            chunk_nums = randChunkNums(self.num_chunks, self.prob, self.seed)
            data = None
            for num in chunk_nums:
                if data is None:
                    data = self.chunk(num)
                else:
                    data = xor(data, self.chunk(num))
        else:
            # 只有一块时直接返回原始数据
            data = self.data
        return Droplet(data, self.seed, self.num_chunks, self.padding)

    def chunk(self, num):
        """返回第 num 个 chunk 的字节数据 (已补零对齐) ."""
        start = self.chunk_size * num
        end = self.chunk_size * (num + 1)
        return self.data[start:end]

    def updateSeed(self):
        """推进随机种子到下一个值."""
        self.seed = self.r()


# ---------------------------------------------------------------------------
# Glass (接收端)
# ---------------------------------------------------------------------------

class Glass:
    """
    喷泉码接收端: 用置信传播 (BP) 译码收到的 droplet，逐步恢复原始 chunk.

    用法
    ----
    g = None
    while not g or not g.isDone():
        d = str2Droplet(droplet_str)
        if g is None:
            g = Glass(d)
        else:
            g.addDroplet(d)
    data = g.getData()   # 恢复的原始数据

    参数
    ----
    d : Droplet   第一个收到的编码包 (用于初始化分块数等元信息)
    """

    def __init__(self, d: Droplet):
        self.entries = []             # 待解析的 [chunk_nums, data] 列表
        self.droplets = []            # 已接收的 Droplet 对象列表 (备用)
        self.num_chunks = d.num_chunks
        self.chunks = [None] * self.num_chunks  # 已恢复的 chunk，None 表示未解出
        self.prob = robust_solition(self.num_chunks)
        self.padding = d.padding
        self.addDroplet(d)

    def addDroplet(self, d):
        """
        加入一个新 droplet 并触发 BP 译码.

        如果 num_chunks == 1，直接赋值；否则通过 updateEntry 做 XOR 消元.
        """
        if self.num_chunks > 1:
            d.prob = self.prob
            self.droplets.append(d)
            entry = [d.chunkNums(), d.data]
            self.entries.append(entry)
            self.updateEntry(entry)
        else:
            self.chunks[0] = d.data

    def updateEntry(self, entry):
        """
        BP 消元核心: 对 entry 执行一步置信传播.

        - 用已知 chunk XOR 消去 entry 中对应的编号
        - 若 entry 只剩 1 个未知 chunk，解出该 chunk 并递归更新其他 entry
        """
        for chunk_num in entry[0]:
            if self.chunks[chunk_num] is not None:
                # 已知 chunk，XOR 消去
                entry[1] = xor(entry[1], self.chunks[chunk_num])
                entry[0].remove(chunk_num)
        if len(entry[0]) == 1:
            # 恢复出新的 chunk
            self.chunks[entry[0][0]] = entry[1]
            self.entries.remove(entry)
            # 通知其他仍包含该 chunk 编号的 entry 更新
            for d in self.entries:
                if entry[0][0] in d[0]:
                    self.updateEntry(d)

    def getData(self):
        """
        返回恢复的原始数据.仅在 isDone() 为 True 时有效.

        会去除最后一块的补零 padding.
        """
        if self.isDone():
            self.chunks[-1] = self.chunks[-1][:-self.padding]
            return b''.join(self.chunks)
        return b''

    def isDone(self):
        """所有 chunk 均已恢复时返回 True."""
        return None not in self.chunks

    def chunksDone(self):
        """返回当前已恢复的 chunk 数量，用于进度显示."""
        return sum(1 for x in self.chunks if x is not None)


# ---------------------------------------------------------------------------
# 模块自测 (仅开发调试用，正常运行不执行循环体)
# ---------------------------------------------------------------------------

b = seeded_random(None)
for i in range(0):
    print(b())

y = seeded_random(None)
for i in range(49, 50):
    a = robust_solition(2 + i)
    for j in range(500):
        tmp = y()

testData = bytes(range(1, 11))
fountain = Fountain(testData, 4)
for _i in range(0):
    ds = fountain.droplet().getStr()
    d = str2Droplet(ds)
    g = Glass(d)
    print(_i, f'完成度: {g.chunksDone()}/{g.num_chunks}')
    while not g.isDone():
        ds = fountain.droplet().getStr()
        print(ds)
        d = str2Droplet(ds)
        g.addDroplet(d)
        print(_i, f'完成度: {g.chunksDone()}/{g.num_chunks}')
    print('解码后的数据:', list(g.getData()))

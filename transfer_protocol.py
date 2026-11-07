"""任务绑定的二维码传输格式: 原始源块/LT 包、逐包 CRC 和最终文件 SHA-256."""

import base64
import binascii
import hashlib
import json
import math
import struct
import zlib
from dataclasses import dataclass

from fountain import Droplet


PREFIX = 'QT2:'
HEADER = struct.Struct('>B32sIIQII')
MAX_BLOCKS = 1_000_000
MAX_CHUNK_SIZE = 2048
SOURCE = 0
FOUNTAIN = 1


class PacketError(ValueError):
    """外部编码包不符合协议或校验失败."""


@dataclass(frozen=True)
class Parameters:
    task_id: str
    chunk_size: int
    num_chunks: int
    payload_size: int

    @property
    def padding(self):
        return self.num_chunks * self.chunk_size - self.payload_size

    @classmethod
    def read(cls, task_id, chunk_size, num_chunks, payload_size):
        """校验来自二维码或现场文件的编码参数."""
        if not isinstance(task_id, str) or len(task_id) != 64:
            raise PacketError('任务标识长度不正确')
        try:
            digest = bytes.fromhex(task_id)
        except ValueError as exc:
            raise PacketError('任务标识不是有效 SHA-256') from exc
        if len(digest) != 32 or task_id != digest.hex():
            raise PacketError('任务标识不是规范的 SHA-256')
        if not all(type(value) is int for value in (chunk_size, num_chunks, payload_size)):
            raise PacketError('编码参数必须是整数')
        if not 1 <= chunk_size <= MAX_CHUNK_SIZE or not 1 <= num_chunks <= MAX_BLOCKS:
            raise PacketError('源块数量或大小超出支持范围')
        if payload_size <= 0 or math.ceil(payload_size / chunk_size) != num_chunks:
            raise PacketError('源块数量与编码长度不一致')
        return cls(task_id, chunk_size, num_chunks, payload_size)


def identity(payload: bytes, chunk_size: int) -> str:
    return hashlib.sha256(b'QRCodeTransceiver-v2\0' + struct.pack('>I', chunk_size) + payload).hexdigest()


def make_payload(filename: str, original: bytes, chunk_size: int, compress: bool) -> bytes:
    """只在建立发送任务时编码一次; 后续补传复用这个精确字节串."""
    body = original
    compression = 'none'
    if compress:
        compressed = zlib.compress(original, level=9)
        if len(compressed) < len(original) * 0.95:
            body, compression = compressed, 'zlib'
    manifest = {
        'version': 2, 'filename': filename, 'size': len(original),
        'sha256': hashlib.sha256(original).hexdigest(),
        'compression': compression, 'chunk_size': chunk_size,
    }
    header = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return struct.pack('>I', len(header)) + header + body


def restore_payload(payload: bytes, parameters: Parameters):
    """恢复原文件前核对完整编码任务、压缩内容和最终文件摘要."""
    if len(payload) != parameters.payload_size or identity(payload, parameters.chunk_size) != parameters.task_id:
        raise PacketError('完整编码数据与任务标识不一致, 不能标记完成')
    if len(payload) < 4:
        raise PacketError('缺少文件清单')
    length = struct.unpack('>I', payload[:4])[0]
    try:
        manifest = json.loads(payload[4:4 + length].decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PacketError('文件清单损坏') from exc
    required = {'version', 'filename', 'size', 'sha256', 'compression', 'chunk_size'}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise PacketError('文件清单字段不正确')
    if manifest['version'] != 2 or manifest['chunk_size'] != parameters.chunk_size:
        raise PacketError('文件清单与编码参数不一致')
    if not isinstance(manifest['filename'], str) or not manifest['filename'] or type(manifest['size']) is not int or manifest['size'] < 0:
        raise PacketError('文件名或原始大小不正确')
    body = payload[4 + length:]
    if manifest['compression'] == 'zlib':
        try:
            body = zlib.decompress(body)
        except zlib.error as exc:
            raise PacketError('文件压缩数据损坏') from exc
    elif manifest['compression'] != 'none':
        raise PacketError('不支持的文件压缩方式')
    if len(body) != manifest['size'] or hashlib.sha256(body).hexdigest() != manifest['sha256']:
        raise PacketError('最终文件 SHA-256 或长度校验失败')
    return manifest, body


@dataclass(frozen=True)
class Packet:
    parameters: Parameters
    kind: int
    number: int
    round_no: int
    data: bytes

    @property
    def key(self):
        # 同一源块跨轮次重发仍是重复信息, 轮号不参与去重标识.
        return hashlib.sha256(struct.pack('>BI', self.kind, self.number) + self.data).digest()

    def text(self):
        params = self.parameters
        raw = HEADER.pack(self.kind, bytes.fromhex(params.task_id), params.chunk_size,
                          params.num_chunks, params.payload_size, self.number, self.round_no) + self.data
        raw += struct.pack('>I', zlib.crc32(raw))
        return PREFIX + base64.urlsafe_b64encode(raw).decode('ascii')

    def droplet(self):
        return Droplet(self.data, self.number, self.parameters.num_chunks, self.parameters.padding)

    @classmethod
    def read(cls, text: str):
        """拒绝截断、错误任务参数、非法源块编号和未通过 CRC 的画面数据."""
        if not text.startswith(PREFIX):
            raise PacketError('不是 QT2 任务包; 旧包没有任务标识, 不能安全续收')
        try:
            raw = base64.b64decode(text[len(PREFIX):], altchars=b'-_', validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PacketError('编码包文本损坏或截断') from exc
        if len(raw) < HEADER.size + 4:
            raise PacketError('编码包长度不足')
        if struct.unpack('>I', raw[-4:])[0] != zlib.crc32(raw[:-4]):
            raise PacketError('编码包 CRC 校验失败')
        kind, task_id, size, count, length, number, round_no = HEADER.unpack(raw[:HEADER.size])
        parameters = Parameters.read(task_id.hex(), size, count, length)
        data = raw[HEADER.size:-4]
        if kind not in (SOURCE, FOUNTAIN) or len(data) != size or round_no < 1:
            raise PacketError('编码包类型、载荷长度或轮号无效')
        if kind == SOURCE and number >= count:
            raise PacketError('补传源块编号超出任务范围')
        return cls(parameters, kind, number, round_no, data)


def is_transfer_text(text: str) -> bool:
    # CRC 在接收任务入口核对, 扫描层保留损坏任务包以便报告校验失败.
    return text.startswith(PREFIX)

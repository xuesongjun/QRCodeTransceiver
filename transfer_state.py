"""不可变发送任务、可续收的接收任务、原子现场保存和可理解的任务日志."""

import base64
import hashlib
import json
import math
import os
import struct
import tempfile
import time
import zlib
from dataclasses import asdict
from pathlib import Path

from fountain import Fountain, Glass
from repair_request import create as create_feedback, read as read_feedback
from transfer_protocol import FOUNTAIN, SOURCE, Packet, PacketError, Parameters, identity, make_payload, restore_payload


MAGIC = b'QRTASK1\0'


def file_digest(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes):
    """同目录临时文件落盘后原子替换; 失败直接报告, 不破坏上次保存的现场."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_snapshot(path: Path, state):
    body = zlib.compress(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
    atomic_write(path, MAGIC + hashlib.sha256(body).digest() + body)


def load_snapshot(path: Path, role):
    raw = path.read_bytes()
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 32:
        raise ValueError('不是受支持的任务现场文件')
    checksum, body = raw[len(MAGIC):len(MAGIC) + 32], raw[len(MAGIC) + 32:]
    if hashlib.sha256(body).digest() != checksum:
        raise ValueError('任务现场损坏或被截断')
    try:
        state = json.loads(zlib.decompress(body).decode('utf-8'))
    except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('任务现场内容损坏') from exc
    if not isinstance(state, dict) or state.get('version') != 1 or state.get('role') != role:
        raise ValueError('任务现场版本或发送/接收角色不匹配')
    return state


def encode_bytes(data):
    return base64.b64encode(data).decode('ascii')


def decode_bytes(text):
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('任务现场含损坏的二进制数据') from exc


class SendTask:
    """冻结已压缩编码数据和分块参数, 补传永不重新读取原文件."""

    def __init__(self, payload: bytes, chunk_size: int, source_path: str, round_no=0):
        count = math.ceil(len(payload) / chunk_size)
        self.parameters = Parameters.read(identity(payload, chunk_size), chunk_size, count, len(payload))
        self.manifest, _ = restore_payload(payload, self.parameters)
        self.payload = payload
        self.source_path = source_path
        self.round_no = round_no
        self.state_path = None

    @classmethod
    def create(cls, path: Path, chunk_size=256, compress=True):
        if not 1 <= chunk_size <= 2048:
            raise ValueError('分块大小应为 1-2048 字节')
        original = path.read_bytes()
        return cls(make_payload(path.name, original, chunk_size, compress), chunk_size, str(path.resolve()))

    def source(self, index):
        start = index * self.parameters.chunk_size
        return self.payload[start:start + self.parameters.chunk_size].ljust(self.parameters.chunk_size, b'\0')

    def rounds(self, requested=None, extra=0.5):
        """每轮完整源块覆盖后发新 LT 修复包; 定向补传只轮播请求的源块."""
        indices = range(self.parameters.num_chunks) if requested is None else tuple(requested)
        if not indices:
            return
        while True:
            self.round_no += 1
            if self.state_path is not None:
                self.save(self.state_path)
            repairs = math.ceil(self.parameters.num_chunks * extra) if requested is None else 0
            total = len(indices) + repairs
            for offset, index in enumerate(indices, start=1):
                yield Packet(self.parameters, SOURCE, index, self.round_no, self.source(index)), offset, total
            seed = int.from_bytes(hashlib.sha256((self.parameters.task_id + str(self.round_no)).encode('ascii')).digest()[:4], 'big')
            fountain = Fountain(self.payload, self.parameters.chunk_size, seed=seed)
            for offset in range(repairs):
                droplet = fountain.droplet()
                yield Packet(self.parameters, FOUNTAIN, droplet.seed, self.round_no, droplet.data), len(indices) + offset + 1, total

    def request(self, code):
        return read_feedback(code, self.parameters.task_id, self.parameters.num_chunks)

    def save(self, path: Path):
        save_snapshot(path, {'version': 1, 'role': 'sender', 'payload': encode_bytes(self.payload),
                             'parameters': asdict(self.parameters), 'source_path': self.source_path, 'round_no': self.round_no})
        self.state_path = path

    @classmethod
    def load(cls, path: Path):
        state = load_snapshot(path, 'sender')
        try:
            parameters = Parameters.read(**state['parameters'])
            task = cls(decode_bytes(state['payload']), parameters.chunk_size, state['source_path'], state['round_no'])
        except (KeyError, TypeError) as exc:
            raise ValueError('发送任务字段缺失或无效') from exc
        if task.parameters != parameters or type(task.round_no) is not int or task.round_no < 0:
            raise ValueError('冻结发送数据与任务参数不一致')
        task.state_path = path
        return task


class ReceiveTask:
    def __init__(self, parameters: Parameters):
        self.parameters = parameters
        self.glass = Glass.empty(parameters.num_chunks, parameters.padding)
        self.seen = set()
        self.videos = set()
        self.events = []
        self.state_path = None
        self.saved_at = None
        self.last_round_finished = False
        self.bad_indices = set()
        self.invalid_packets = 0
        self.duplicate_packets = 0
        self.errors = {}
        self.verified = False
        self.output_path = None
        self.manifest = None
        self.integrity_error = ''

    @property
    def done(self):
        return self.glass.chunksDone()

    @property
    def missing(self):
        return [index for index, data in enumerate(self.glass.chunks) if data is None]

    @property
    def label(self):
        return self.manifest['filename'] if self.manifest is not None else '任务 ' + self.parameters.task_id[:12]

    def log(self, action, **details):
        self.events.append({'time': time.time(), 'action': action, 'task_id': self.parameters.task_id, **details})

    def feed(self, text):
        """任务入口验证外部包; 坏包不会成为译码关系, 重复包不计新增信息."""
        try:
            packet = Packet.read(text)
        except PacketError as exc:
            self.invalid_packets += 1
            self.errors[str(exc)] = self.errors.get(str(exc), 0) + 1
            return False
        if packet.parameters != self.parameters:
            self.invalid_packets += 1
            self.errors['拒绝其他任务'] = self.errors.get('拒绝其他任务', 0) + 1
            return False
        if packet.key in self.seen:
            self.duplicate_packets += 1
            return False
        try:
            if packet.kind == SOURCE:
                self.glass.addChunk(packet.number, packet.data)
            else:
                self.glass.addDroplet(packet.droplet())
        except ValueError as exc:
            self.invalid_packets += 1
            if packet.kind == SOURCE:
                self.bad_indices.add(packet.number)
            self.errors[str(exc)] = self.errors.get(str(exc), 0) + 1
            return False
        self.seen.add(packet.key)
        self.last_round_finished = False
        return True

    def finish(self):
        self.glass.solve()
        self.last_round_finished = True
        if self.glass.isDone():
            try:
                self.manifest, _ = restore_payload(self.glass.getData(), self.parameters)
            except PacketError as exc:
                self.integrity_error = str(exc)
                raise
            self.integrity_error = ''
            self.verified = True

    def write_output(self, directory: Path, overwrite=False):
        self.finish()
        if not self.verified:
            raise ValueError('尚有未恢复源块, 不写出未完成文件')
        manifest, data = restore_payload(self.glass.getData(), self.parameters)
        name = Path(manifest['filename']).name
        if not name or name in ('.', '..'):
            raise ValueError('接收文件名不可用')
        target = directory / name
        if target.exists() and not overwrite:
            if file_digest(target) == manifest['sha256']:
                self.output_path = str(target)
                return target
            target = directory / f'{target.stem}_{time.time_ns()}{target.suffix}'
        atomic_write(target, data)
        self.output_path = str(target)
        return target

    def feedback(self, mode='auto', scope=None):
        self.finish()
        feedback = create_feedback(self.parameters.task_id, self.parameters.num_chunks, self.missing, mode, scope)
        self.log('生成补传请求', mode=feedback.description, code_length=len(feedback.code), requested=len(feedback.requested),
                 total=self.parameters.num_chunks, restored=self.done, missing=len(self.missing))
        return feedback

    def save(self, path: Path):
        saved_at = time.time()
        state = {
            'version': 1, 'role': 'receiver', 'parameters': asdict(self.parameters),
            'chunks': [encode_bytes(data) if data is not None else None for data in self.glass.chunks],
            'entries': [[indices, encode_bytes(data)] for indices, data in self.glass.entries],
            'seeds': {str(seed): digest.hex() for seed, digest in self.glass._seeds.items()},
            'seen': sorted(digest.hex() for digest in self.seen), 'videos': sorted(self.videos),
            'events': self.events + [{'time': saved_at, 'action': '现场已保存', 'task_id': self.parameters.task_id,
                                     'file': self.label, 'path': str(path), 'restored': self.done,
                                     'total': self.parameters.num_chunks, 'missing': len(self.missing)}],
            'saved_at': saved_at, 'last_round_finished': self.last_round_finished,
            'bad_indices': sorted(self.bad_indices), 'invalid_packets': self.invalid_packets,
            'duplicate_packets': self.duplicate_packets, 'output_path': self.output_path,
            'errors': self.errors,
        }
        save_snapshot(path, state)
        self.state_path, self.saved_at = path, saved_at
        self.events = state['events']

    @classmethod
    def load(cls, path: Path):
        state = load_snapshot(path, 'receiver')
        try:
            task = cls(Parameters.read(**state['parameters']))
            if len(state['chunks']) != task.parameters.num_chunks:
                raise ValueError('现场源块数量不一致')
            task.glass.chunks = [decode_bytes(data) if data is not None else None for data in state['chunks']]
            for data in task.glass.chunks:
                if data is not None and len(data) != task.parameters.chunk_size:
                    raise ValueError('现场源块长度不一致')
            task.glass.entries = []
            for indices, encoded in state['entries']:
                if not indices or len(set(indices)) != len(indices) or any(type(index) is not int or not 0 <= index < task.parameters.num_chunks for index in indices):
                    raise ValueError('现场编码关系的编号无效')
                data = decode_bytes(encoded)
                if len(data) != task.parameters.chunk_size:
                    raise ValueError('现场编码关系的长度无效')
                task.glass.entries.append([indices, data])
            task.glass._seeds = {int(seed): bytes.fromhex(digest) for seed, digest in state['seeds'].items()}
            task.seen = {bytes.fromhex(digest) for digest in state['seen']}
            task.videos = set(state['videos'])
            if any(len(digest) != 32 for digest in task.seen) or any(len(digest) != 32 for digest in task.glass._seeds.values()):
                raise ValueError('现场去重摘要长度不正确')
            task.events = state['events']
            task.bad_indices = set(state['bad_indices'])
            if any(type(index) is not int or not 0 <= index < task.parameters.num_chunks for index in task.bad_indices):
                raise ValueError('现场异常源块编号无效')
            task.invalid_packets = state['invalid_packets']
            task.duplicate_packets = state['duplicate_packets']
            task.errors = state['errors']
            task.output_path = state['output_path']
            task.saved_at = state['saved_at']
        except (KeyError, TypeError) as exc:
            raise ValueError('接收现场字段缺失或无效') from exc
        task.glass.restore(task.glass.chunks, task.glass.entries, task.glass._seeds)
        try:
            task.finish()
        except PacketError:
            # 校验失败的现场也允许重新打开, 但绝不能标记完成或导出文件.
            pass
        task.last_round_finished = state['last_round_finished']
        task.state_path = path
        return task

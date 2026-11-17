"""紧凑单行补传请求: 缺块/成功集合、差分/区间/位图、zlib 和 CRC32."""

import base64
import binascii
import struct
import zlib
from dataclasses import dataclass


PREFIX = 'QRF1:'
HEAD = struct.Struct('>BB32sI')
MODES = {'missing': 0, 'success': 1, 'all': 2}
CODECS = {'delta': 0, 'ranges': 1, 'bitmap': 2}


class RequestError(ValueError):
    """外部补传请求损坏、越界或不属于当前发送任务."""


def ranges(indices):
    result = []
    for index in indices:
        if result and result[-1][0] + result[-1][1] == index:
            result[-1] = (result[-1][0], result[-1][1] + 1)
        else:
            result.append((index, 1))
    return result


def integers(values):
    values = list(values)
    return struct.pack('>I', len(values)) + b''.join(struct.pack('>I', value) for value in values)


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, count):
        end = self.offset + count
        if end > len(self.data):
            raise RequestError('补传码被截断')
        data = self.data[self.offset:end]
        self.offset = end
        return data

    def uint(self):
        return struct.unpack('>I', self.take(4))[0]

    def uints(self):
        count = self.uint()
        # 按剩余字节数限制外部计数, 防止截断输入创建巨大循环.
        if count > (len(self.data) - self.offset) // 4:
            raise RequestError('补传集合计数与长度不一致')
        return [self.uint() for _ in range(count)]


def wrap(raw):
    candidates = []
    for flag, stored in ((0, raw), (1, zlib.compress(raw, level=9))):
        body = bytes([flag]) + stored
        body += struct.pack('>I', zlib.crc32(body))
        candidates.append(PREFIX + base64.urlsafe_b64encode(body).decode('ascii'))
    return min(candidates, key=len)


@dataclass(frozen=True)
class Feedback:
    code: str
    task_id: str
    total: int
    requested: tuple
    mode: str
    codec: str
    scope: tuple

    @property
    def description(self):
        mode = {'missing': '未恢复块', 'success': '已恢复块的补集', 'all': '全部重发'}[self.mode]
        codec = {'delta': '编号差分', 'ranges': '连续区间', 'bitmap': '位图(1=待补传)'}[self.codec]
        return f'{mode}, {codec}'


def create(task_id: str, total: int, missing, mode='auto', scope=None) -> Feedback:
    """比较整个单行补传码的实际长度; 不把全量重发加入自动候选."""
    if mode not in ('auto', 'missing', 'success', 'bitmap', 'all'):
        raise RequestError('未知补传描述方式')
    if not 1 <= total <= 1_000_000:
        raise RequestError('无效源块总数')
    if len(task_id) != 64:
        raise RequestError('无效任务标识')
    try:
        task_bytes = bytes.fromhex(task_id)
    except ValueError as exc:
        raise RequestError('无效任务标识') from exc
    if len(task_bytes) != 32:
        raise RequestError('无效任务标识')
    selected = set(range(total)) if scope is None else set(scope)
    unknown = set(missing)
    if any(type(index) is not int or not 0 <= index < total for index in selected | unknown):
        raise RequestError('源块编号超出任务范围')
    unknown &= selected
    if mode == 'all':
        selected, unknown = set(range(total)), set(range(total))
    if not selected:
        raise RequestError('补传选区为空')
    scope_bytes = integers(value for pair in ranges(sorted(selected)) for value in pair) if len(selected) != total else integers([])
    directions = ('missing', 'success') if mode == 'auto' else ('missing',) if mode == 'bitmap' else (mode,)
    candidates = []
    for direction in directions:
        values = sorted(selected - unknown if direction == 'success' else unknown)
        codecs = ('delta', 'ranges', 'bitmap') if direction == 'missing' else ('delta', 'ranges')
        if mode == 'bitmap':
            codecs = ('bitmap',)
        if mode == 'all':
            codecs = ('delta',)
        for codec in codecs:
            if direction == 'all':
                payload = integers([])
            elif codec == 'delta':
                previous = 0
                deltas = []
                for index in values:
                    deltas.append(index - previous)
                    previous = index
                payload = integers(deltas)
            elif codec == 'ranges':
                payload = integers(value for pair in ranges(values) for value in pair)
            else:
                bitmap = bytearray((len(selected) + 7) // 8)
                for bit, index in enumerate(sorted(selected)):
                    if index in unknown:
                        bitmap[bit // 8] |= 1 << (bit % 8)
                payload = bytes(bitmap)
            raw = HEAD.pack(MODES[direction], CODECS[codec], task_bytes, total) + scope_bytes + payload
            code = wrap(raw)
            candidates.append(Feedback(code, task_id, total, tuple(sorted(unknown)), direction, codec, tuple(sorted(selected))))
    return min(candidates, key=lambda feedback: len(feedback.code))


def read(code: str, task_id: str, total: int) -> Feedback:
    code = code.strip()
    if not code.startswith(PREFIX):
        raise RequestError('不支持的补传码版本')
    try:
        encoded = base64.b64decode(code[len(PREFIX):], altchars=b'-_', validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RequestError('补传码文本损坏或复制不完整') from exc
    if len(encoded) < 5 or struct.unpack('>I', encoded[-4:])[0] != zlib.crc32(encoded[:-4]):
        raise RequestError('补传码校验失败, 请完整复制或导入请求文件')
    flag, data = encoded[0], encoded[1:-4]
    if flag == 1:
        try:
            decoder = zlib.decompressobj()
            data = decoder.decompress(data, 16_000_001)
            if len(data) > 16_000_000 or not decoder.eof or decoder.unused_data:
                raise RequestError('补传码解压长度无效')
        except zlib.error as exc:
            raise RequestError('补传码压缩数据损坏') from exc
    elif flag != 0:
        raise RequestError('不支持的补传码封装')
    reader = Reader(data)
    direction, codec, source_id, source_total = HEAD.unpack(reader.take(HEAD.size))
    if source_id.hex() != task_id or source_total != total:
        raise RequestError('补传请求与当前发送任务不匹配')
    if direction not in MODES.values() or codec not in CODECS.values():
        raise RequestError('未知块集合解释方式')
    scope_values = reader.uints()
    selected = set(range(total))
    if scope_values:
        selected = expand_ranges(scope_values, total)
    if direction == MODES['all']:
        if reader.uints() or codec != CODECS['delta'] or scope_values:
            raise RequestError('全部重发请求格式不正确')
        requested = selected
    elif codec == CODECS['bitmap']:
        if direction != MODES['missing']:
            raise RequestError('位图约定为 1=待补传, 0=本次不补传')
        bitmap = reader.take((len(selected) + 7) // 8)
        if selected and len(selected) % 8 and bitmap[-1] >> (len(selected) % 8):
            raise RequestError('位图超出源块范围的位必须为 0')
        requested = {index for bit, index in enumerate(sorted(selected)) if bitmap[bit // 8] & (1 << (bit % 8))}
    else:
        values = reader.uints()
        if codec == CODECS['delta']:
            indices = []
            previous = 0
            for offset, delta in enumerate(values):
                if offset and delta == 0:
                    raise RequestError('差分编号重复')
                previous += delta
                indices.append(previous)
            listed = set(indices)
        else:
            listed = expand_ranges(values, total)
        if not listed <= selected:
            raise RequestError('补传集合包含非法或超出选区的源块编号')
        requested = selected - listed if direction == MODES['success'] else listed
    if reader.offset != len(data):
        raise RequestError('补传码有多余或损坏的数据')
    mode_name = next(name for name, value in MODES.items() if value == direction)
    codec_name = next(name for name, value in CODECS.items() if value == codec)
    return Feedback(code, task_id, total, tuple(sorted(requested)), mode_name, codec_name, tuple(sorted(selected)))


def expand_ranges(values, total):
    if len(values) % 2:
        raise RequestError('区间描述被截断')
    result = set()
    previous_end = 0
    for start, count in zip(values[::2], values[1::2]):
        if count == 0 or start < previous_end or start + count > total:
            raise RequestError('区间编号重叠或越界')
        result.update(range(start, start + count))
        previous_end = start + count
    return result

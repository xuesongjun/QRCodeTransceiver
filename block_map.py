"""将真实源块连续分组为最多 100 格, 显示分组不改变传输粒度."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Cell:
    start: int
    end: int
    restored: int
    missing: tuple
    color: str
    reason: str


def cells(task):
    total = task.parameters.num_chunks
    count = min(100, total)
    result = []
    for cell in range(count):
        start, end = cell * total // count, (cell + 1) * total // count
        missing = tuple(index for index in range(start, end) if task.glass.chunks[index] is None)
        restored = end - start - len(missing)
        if task.bad_indices.intersection(range(start, end)):
            color, reason = 'red', '本组有明确的源块载荷冲突'
        elif not missing:
            color, reason = 'green', '本组源块全部恢复'
        elif task.last_round_finished:
            color, reason = 'red', '本轮结束仍待补传, 不表示这些块校验错误'
        elif restored:
            color, reason = 'yellow', '本组部分源块已恢复'
        else:
            color, reason = 'gray', '尚未恢复, 不推断是否已收到相关喷泉信息'
        result.append(Cell(start, end, restored, missing, color, reason))
    return result

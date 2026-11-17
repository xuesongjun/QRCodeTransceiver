"""剥皮停滞时的 GF(2) 求解: 满秩、欠秩、矛盾及独立穷举参照."""

import copy
import random
import unittest

from fountain import Droplet, Glass, xor


def equation(indices, chunks):
    value = bytes(len(chunks[0]))
    for index in indices:
        value = xor(value, chunks[index])
    return [list(indices), value]


class GaussianTests(unittest.TestCase):
    def test_real_lt_packets_full_rank_with_no_singleton(self):
        packets = [Droplet(b'\x77', 2, 3, 0), Droplet(b'\x22', 10, 3, 0), Droplet(b'\x66', 12, 3, 0)]
        for sequence in (packets, list(reversed(packets))):
            glass = Glass.empty(3, 0)
            for packet in sequence + sequence:
                glass.addDroplet(packet)
            self.assertEqual(glass.chunksDone(), 0)
            report = glass.solve()
            self.assertEqual(report, {'peeling': 0, 'gaussian': 3, 'rank': 3, 'unknowns': 3})
            self.assertEqual(glass.getData(), b'\x11\x33\x55')
            self.assertEqual(glass.entries, [])
            self.assertEqual(glass.solve()['gaussian'], 0)
            self.assertEqual(glass.getData(), b'\x11\x33\x55')

    def test_deficient_rank_restores_only_uniquely_determined_chunks(self):
        glass = Glass(Droplet(b'\x22', 10, 3, 0))
        glass.addDroplet(Droplet(b'\x77', 2, 3, 0))
        report = glass.solve()
        self.assertEqual(report['rank'], 2)
        self.assertEqual(glass.chunks, [None, None, b'\x55'])
        self.assertFalse(glass.isDone())
        glass.addChunk(0, b'\x11')
        self.assertEqual(glass.getData(), b'\x11\x33\x55')

    def test_dependent_rows_do_not_become_false_rank_or_pollute_on_conflict(self):
        glass = Glass.empty(3, 0)
        chunks = [b'\x11', b'\x33', b'\x55']
        glass.entries = [equation([0, 1], chunks), equation([1, 2], chunks)] * 3
        self.assertEqual(glass.solve()['rank'], 2)
        self.assertEqual(glass.chunksDone(), 0)
        self.assertEqual(len(glass.entries), 2)
        # AB/BC/AC 在实数域满秩, 但在 GF(2) 上只有秩 2.
        glass.entries.append(equation([0, 2], chunks))
        self.assertEqual(glass.solve()['rank'], 2)
        self.assertEqual(glass.chunksDone(), 0)
        glass.entries.append([[0, 2], b'\xff'])
        original = copy.deepcopy(glass.entries)
        with self.assertRaisesRegex(ValueError, '矛盾'):
            glass.solve()
        self.assertEqual(glass.chunks, [None] * 3)
        self.assertEqual(glass.entries, original)

    def test_746_chunks_73_percent_peeling_with_920_observations(self):
        # 合成规模回归, 不是用户那份未提供的原始视频/编码包.
        chunks = [random.Random(index).randbytes(32) for index in range(746)]
        glass = Glass.empty(746, 0)
        for index in range(544):
            glass.addChunk(index, chunks[index])
        rows = [equation([i, i + 1], chunks) for i in range(544, 745)]
        rows.append(equation([544, 545, 546], chunks))
        glass.entries = rows + rows[:174]
        self.assertEqual(544 + len(glass.entries), 920)
        self.assertEqual(glass.chunksDone(), 544)
        report = glass.solve()
        self.assertEqual((report['gaussian'], report['rank'], report['unknowns']), (202, 202, 202))
        self.assertEqual(glass.getData(), b''.join(chunks))

    def test_matches_exhaustive_binary_solutions_for_small_random_systems(self):
        rng = random.Random(423)
        for size in range(3, 8):
            for _ in range(15):
                truth = rng.randrange(1 << size)
                masks = [rng.randrange(1, 1 << size) for _ in range(rng.randrange(1, size + 4))]
                rhs = [(truth & mask).bit_count() % 2 for mask in masks]
                solutions = [value for value in range(1 << size)
                             if all((value & mask).bit_count() % 2 == data for mask, data in zip(masks, rhs))]
                expected = []
                for column in range(size):
                    values = {(value >> column) & 1 for value in solutions}
                    expected.append(bytes(values) if len(values) == 1 else None)
                glass = Glass.empty(size, 0)
                glass.entries = [[[column for column in range(size) if mask & (1 << column)], bytes([data])]
                                 for mask, data in zip(masks, rhs)]
                glass.solve()
                self.assertEqual(glass.chunks, expected)

    def test_payload_width_leading_zeros_and_padding(self):
        chunks = [b'\0\x11\x80', b'\0\x33\xff', b'\0\x55\0']
        glass = Glass.empty(3, 1)
        glass.entries = [equation(indices, chunks) for indices in ([0, 1], [1, 2], [0, 1, 2])]
        glass.solve()
        self.assertEqual(glass.getData(), b''.join(chunks)[:-1])


if __name__ == '__main__':
    unittest.main()

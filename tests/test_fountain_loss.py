"""确认喷泉编码包可丢失、乱序接收, 不依赖上一包的结果."""

import random
import unittest

from fountain import Droplet, Fountain, Glass, str2Droplet


class FountainLossTests(unittest.TestCase):
    def test_three_blocks_reach_fixed_point_without_an_extra_packet(self):
        packets = [Droplet(bytes([0x22]), 10, 3, 0), Droplet(bytes([0x44]), 7, 3, 0), Droplet(bytes([0x11]), 3, 3, 0)]
        glass = Glass(packets[0])
        for packet in packets[1:]:
            glass.addDroplet(packet)
        self.assertEqual(glass.chunksDone(), 3)
        self.assertEqual(glass.getData(), bytes([0x11, 0x33, 0x55]))
        self.assertEqual(glass.getData(), bytes([0x11, 0x33, 0x55]))
        for packet in reversed(packets):
            glass.addDroplet(packet)
        self.assertEqual(glass.getData(), bytes([0x11, 0x33, 0x55]))
        self.assertEqual(glass.entries, [])

    def test_missing_and_reordered_packets_still_restore_data(self):
        original = random.Random(42).randbytes(4097)
        fountain = Fountain(original, chunk_size=128)
        packets = [fountain.droplet().getStr() for _ in range(300)]
        # 丢弃开头及后续每五包中的一包, 再打乱接收顺序.
        available = [text for index, text in enumerate(packets) if index % 5 != 0]
        random.Random(7).shuffle(available)
        glass = Glass(str2Droplet(available[0]))
        for packet in available[1:]:
            if glass.isDone():
                break
            glass.addDroplet(str2Droplet(packet))
        self.assertTrue(glass.isDone())
        self.assertEqual(glass.getData(), original)


if __name__ == '__main__':
    unittest.main()

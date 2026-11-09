"""现场恢复、精确补传、重复/损坏输入和文件最终校验的完整纵向验证."""

import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from itertools import islice

from block_map import cells
from fountain import Fountain
from repair_request import HEAD, RequestError, create, integers, read, wrap
from transfer_protocol import FOUNTAIN, SOURCE, Packet, PacketError, Parameters
from transfer_state import ReceiveTask, SendTask


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='task-test-')
        self.root = Path(self.temp.name)
        self.file = self.root / 'original.bin'
        self.original = random.Random(5).randbytes(4097)
        self.file.write_bytes(self.original)
        self.sender = SendTask.create(self.file, 256, False)

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_full_rank_snapshot_and_feedback_use_existing_equations_first(self):
        from video_samples import stalled_sample
        sender, body, packets, _ = stalled_sample(self.root)
        task = ReceiveTask(sender.parameters)
        for packet in packets:
            task.feed(packet.text())
        self.assertEqual(task.done, 0)
        path = self.root / 'full-rank.qrrx'
        task.save(path)
        reopened = ReceiveTask.load(path)
        self.assertTrue(reopened.verified)
        self.assertEqual(reopened.write_output(self.root / 'full-rank-output').read_bytes(), body)
        feedback = task.feedback()
        self.assertEqual(feedback.requested, ())
        self.assertEqual(task.done, 3)
        self.assertEqual(task.events[-1]['solving']['gaussian'], 3)

    def test_deficient_snapshot_retains_relations_for_later_source_repair(self):
        from video_samples import stalled_sample
        sender, body, packets, _ = stalled_sample(self.root)
        task = ReceiveTask(sender.parameters)
        # ABC 和 AB 唯一确定 C, A/B 仍需一份独立信息, 不能猜出它们.
        for packet in packets[:2]:
            task.feed(packet.text())
        feedback = task.feedback()
        self.assertEqual(task.done, 1)
        self.assertEqual(feedback.requested, (0, 1))
        path = self.root / 'deficient.qrrx'
        task.save(path)
        reopened = ReceiveTask.load(path)
        reopened.feed(Packet(sender.parameters, SOURCE, 0, 2, sender.source(0)).text())
        reopened.finish()
        self.assertTrue(reopened.verified)
        self.assertEqual(reopened.write_output(self.root / 'deficient-output').read_bytes(), body)

    def packet(self, index, round_no=1):
        return Packet(self.sender.parameters, SOURCE, index, round_no, self.sender.source(index)).text()

    def test_save_restart_request_repair_and_final_file(self):
        tx_path, rx_path = self.root / 'send.qrtx', self.root / 'receive.qrrx'
        self.sender.save(tx_path)
        receiver = ReceiveTask(self.sender.parameters)
        for index in range(0, receiver.parameters.num_chunks, 2):
            receiver.feed(self.packet(index))
        receiver.finish()
        receiver.save(rx_path)
        count, seen = receiver.done, len(receiver.seen)
        receiver = ReceiveTask.load(rx_path)
        self.assertEqual((receiver.done, len(receiver.seen)), (count, seen))
        for index in range(0, receiver.parameters.num_chunks, 2):
            receiver.feed(self.packet(index, 2))
        self.assertEqual((receiver.done, len(receiver.seen)), (count, seen))
        feedback = receiver.feedback()
        sender = SendTask.load(tx_path)
        requested = sender.request(feedback.code).requested
        self.assertEqual(list(requested), receiver.missing)
        for index in reversed(requested):
            receiver.feed(self.packet(index, 3))
            receiver.feed(self.packet(index, 4))
        receiver.finish()
        self.assertTrue(receiver.verified)
        target = receiver.write_output(self.root / 'received')
        self.assertEqual(target.read_bytes(), self.original)
        receiver.save(rx_path)
        self.assertTrue(ReceiveTask.load(rx_path).verified)

    def test_frozen_sender_ignores_later_file_changes(self):
        path = self.root / 'frozen.qrtx'
        self.sender.save(path)
        self.file.write_bytes(b'changed original file')
        restored = SendTask.load(path)
        self.assertEqual(restored.payload, self.sender.payload)
        self.assertEqual(restored.parameters, self.sender.parameters)
        self.assertNotEqual(SendTask.create(self.file, 256).parameters.task_id, self.sender.parameters.task_id)

    def test_wrong_task_same_name_size_and_block_count_is_rejected(self):
        receiver = ReceiveTask(self.sender.parameters)
        changed = bytearray(self.original)
        changed[0] ^= 1
        self.file.write_bytes(changed)
        other = SendTask.create(self.file, 256, False)
        self.assertEqual(other.parameters.num_chunks, self.sender.parameters.num_chunks)
        self.assertFalse(receiver.feed(Packet(other.parameters, SOURCE, 0, 1, other.source(0)).text()))
        self.assertEqual(receiver.done, 0)
        with self.assertRaises(RequestError):
            other.request(receiver.feedback().code)

    def test_corrupt_packet_is_ignored_without_attributing_a_block(self):
        receiver = ReceiveTask(self.sender.parameters)
        text = self.packet(0)
        damaged = text[:-5] + ('A' if text[-5] != 'A' else 'B') + text[-4:]
        self.assertFalse(receiver.feed(damaged))
        self.assertEqual(receiver.done, 0)
        self.assertEqual(receiver.bad_indices, set())
        self.assertEqual(receiver.invalid_packets, 1)

    def test_snapshot_damage_and_save_failure_are_explicit(self):
        receiver = ReceiveTask(self.sender.parameters)
        receiver.feed(self.packet(0))
        path = self.root / 'state.qrrx'
        receiver.save(path)
        original, saved_at = path.read_bytes(), receiver.saved_at
        with patch('transfer_state.os.replace', side_effect=OSError('disk write failed')):
            with self.assertRaises(OSError):
                receiver.save(path)
        self.assertEqual(receiver.saved_at, saved_at)
        self.assertEqual(path.read_bytes(), original)
        path.write_bytes(original[:-1])
        with self.assertRaisesRegex(ValueError, '损坏或被截断'):
            ReceiveTask.load(path)

    def test_final_hash_failure_is_not_completion(self):
        receiver = ReceiveTask(self.sender.parameters)
        for index in range(receiver.parameters.num_chunks):
            data = self.sender.source(index)
            if index == receiver.parameters.num_chunks - 1:
                data = bytes([data[0] ^ 1]) + data[1:]
            receiver.feed(Packet(self.sender.parameters, SOURCE, index, 1, data).text())
        with self.assertRaises(PacketError):
            receiver.finish()
        self.assertFalse(receiver.verified)
        path = self.root / 'failed-final.qrrx'
        receiver.save(path)
        reopened = ReceiveTask.load(path)
        self.assertFalse(reopened.verified)
        self.assertTrue(reopened.integrity_error)

    def test_pending_fountain_relations_survive_restart_and_raw_repair_unlocks_them(self):
        fountain = Fountain(self.sender.payload, 256, seed=29)
        droplet = fountain.droplet()
        droplet.prob = fountain.prob
        while len(droplet.chunkNums()) < 2:
            droplet = fountain.droplet()
            droplet.prob = fountain.prob
        receiver = ReceiveTask(self.sender.parameters)
        text = Packet(self.sender.parameters, FOUNTAIN, droplet.seed, 1, droplet.data).text()
        receiver.feed(text)
        self.assertEqual(receiver.done, 0)
        self.assertTrue(receiver.glass.entries)
        path = self.root / 'pending.qrrx'
        receiver.save(path)
        receiver = ReceiveTask.load(path)
        self.assertFalse(receiver.feed(text))
        omitted = droplet.chunkNums()[-1]
        for index in range(receiver.parameters.num_chunks):
            if index != omitted:
                receiver.feed(self.packet(index))
        receiver.finish()
        self.assertTrue(receiver.verified)
        self.assertEqual(receiver.write_output(self.root / 'pending-output').read_bytes(), self.original)

    def test_multiple_repairs_and_block_map_request_precise_missing_sources(self):
        receiver = ReceiveTask(self.sender.parameters)
        total = receiver.parameters.num_chunks
        self.assertTrue(all(cell.color == 'gray' for cell in cells(receiver)))
        receiver.feed(self.packet(0))
        receiver.finish()
        states = cells(receiver)
        self.assertEqual(states[0].color, 'green')
        self.assertEqual(states[-1].color, 'red')
        self.assertIn('不表示', states[-1].reason)
        for index in receiver.missing[:4]:
            receiver.feed(self.packet(index, 2))
        first = receiver.feedback()
        receiver.save(self.root / 'multi.qrrx')
        receiver = ReceiveTask.load(self.root / 'multi.qrrx')
        selected = {index for cell in cells(receiver)[-3:] for index in range(cell.start, cell.end)}
        feedback = receiver.feedback(scope=selected)
        self.assertEqual(set(feedback.requested), selected & set(receiver.missing))
        for packet, _, _ in islice(self.sender.rounds(feedback.requested), len(feedback.requested)):
            receiver.feed(packet.text())
        second = receiver.feedback()
        self.assertLess(len(second.requested), len(first.requested))
        for index in second.requested:
            receiver.feed(self.packet(index, 9))
        receiver.finish()
        self.assertTrue(receiver.verified)
        self.assertEqual(sum(cell.end - cell.start for cell in cells(receiver)), total)
        self.assertTrue(all(cell.color == 'green' for cell in cells(receiver)))

    def test_empty_file_and_chunk_parameters_have_distinct_tasks(self):
        self.file.write_bytes(b'')
        sender = SendTask.create(self.file, 128)
        self.assertNotEqual(sender.parameters.task_id, SendTask.create(self.file, 256).parameters.task_id)
        receiver = ReceiveTask(sender.parameters)
        for packet, _, _ in islice(sender.rounds(extra=0), sender.parameters.num_chunks):
            receiver.feed(packet.text())
        receiver.finish()
        self.assertEqual(receiver.write_output(self.root / 'empty-output').read_bytes(), b'')

    def test_grouped_map_is_contiguous_at_most_100_and_distinguishes_conflicts(self):
        task = ReceiveTask(Parameters.read('ab' * 32, 1, 251, 251))
        task.feed(Packet(task.parameters, SOURCE, 0, 1, b'A').text())
        states = cells(task)
        self.assertEqual(len(states), 100)
        self.assertEqual(states[0].color, 'yellow')
        self.assertEqual([(a.end, b.start) for a, b in zip(states, states[1:])], [(a.end, a.end) for a in states[:-1]])
        self.assertEqual((states[0].start, states[-1].end), (0, 251))
        task.feed(Packet(task.parameters, SOURCE, 0, 2, b'B').text())
        self.assertEqual(cells(task)[0].color, 'red')
        self.assertIn('冲突', cells(task)[0].reason)


class FeedbackTests(unittest.TestCase):
    def test_patterns_directions_and_actual_shortest_full_text(self):
        task_id = '12' * 32
        total = 129
        patterns = [[], [0], list(range(total)), list(range(total - 1)), list(range(64)), list(range(0, total, 2)), random.Random(8).sample(range(total), 65)]
        for missing in patterns:
            for mode in ('auto', 'missing', 'success', 'bitmap', 'all'):
                with self.subTest(missing=len(missing), mode=mode):
                    feedback = create(task_id, total, missing, mode)
                    result = read(feedback.code, task_id, total)
                    expected = list(range(total)) if mode == 'all' else sorted(missing)
                    self.assertEqual(list(result.requested), expected)
            automatic = create(task_id, total, missing)
            self.assertEqual(len(automatic.code), min(len(create(task_id, total, missing, mode).code) for mode in ('missing', 'success', 'bitmap')))

    def test_selected_regions_request_only_exact_missing_sources(self):
        task_id = 'ab' * 32
        missing, selected = set(range(0, 100, 2)), set(range(20, 40)) | set(range(70, 90))
        for mode in ('auto', 'missing', 'success', 'bitmap'):
            feedback = create(task_id, 100, missing, mode, selected)
            self.assertEqual(set(read(feedback.code, task_id, 100).requested), missing & selected)

    def test_corrupt_truncated_wrong_task_and_illegal_index(self):
        feedback = create('12' * 32, 20, [1, 4, 7])
        with self.assertRaises(RequestError):
            read(feedback.code[:-2], '12' * 32, 20)
        with self.assertRaises(RequestError):
            read(feedback.code, '13' * 32, 20)
        with self.assertRaises(RequestError):
            create('12' * 32, 20, [20])
        with self.assertRaises(RequestError):
            create('12' * 32, 20, [-1])
        header = HEAD.pack(0, 0, bytes.fromhex('12' * 32), 20)
        code = wrap(header + integers([]) + integers([21]))
        with self.assertRaisesRegex(RequestError, '非法'):
            read(code, '12' * 32, 20)


if __name__ == '__main__':
    unittest.main()

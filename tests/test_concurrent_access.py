"""Kiểm tra nhiều luồng xin/trả quyền, không phụ thuộc thời gian demo."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from core.bank_service import BankService, GRANTED, QUEUED


class ConcurrentAccessTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("core.bank_service.log_event")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bank = BankService(database_url="test-only")

    def test_simultaneous_requests_grant_one_and_preserve_fifo(self):
        # Mỗi vòng đều bắt đầu rảnh; barrier cho các luồng cùng xin quyền.
        with ThreadPoolExecutor(max_workers=12) as pool:
            for _ in range(30):
                barrier = threading.Barrier(12)

                def request(node_id):
                    barrier.wait(timeout=3)
                    return node_id, self.bank.request_lock(node_id)

                results = list(pool.map(request, range(12)))
                winners = [nid for nid, result in results if result == GRANTED]
                self.assertEqual(1, len(winners))
                self.assertEqual(11, sum(result == QUEUED for _, result in results))
                queue_order = list(self.bank._lock_queue)
                self.assertEqual(set(range(12)) - set(winners), set(queue_order))
                self.assertEqual(11, len(queue_order))
                holder = winners[0]
                for expected in queue_order:
                    holder = self.bank.release_lock(holder)
                    self.assertEqual(expected, holder)
                self.assertIsNone(self.bank.release_lock(holder))
                self.assertIsNone(self.bank._lock_holder)

    def test_release_racing_new_request_cannot_bypass_waiting_node(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            for _ in range(100):
                self.bank.reset_lock()
                self.assertEqual(GRANTED, self.bank.request_lock(0))
                self.assertEqual(QUEUED, self.bank.request_lock(1))
                barrier = threading.Barrier(2)

                def release():
                    barrier.wait(timeout=3)
                    return self.bank.release_lock(0)

                def request():
                    barrier.wait(timeout=3)
                    return self.bank.request_lock(2)

                released = pool.submit(release)
                requested = pool.submit(request)
                self.assertEqual(1, released.result(timeout=3))
                self.assertEqual(QUEUED, requested.result(timeout=3))
                self.assertEqual(1, self.bank._lock_holder)
                self.assertEqual([2], self.bank._lock_queue)

    def test_wrong_releases_cannot_change_holder_or_fifo(self):
        self.bank.request_lock(0)
        self.bank.request_lock(1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.bank.release_lock, range(2, 30)))
        self.assertTrue(all(result is None for result in results))
        self.assertEqual(0, self.bank._lock_holder)
        self.assertEqual([1], self.bank._lock_queue)

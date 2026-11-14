"""Kiểm tra đếm ngược giữa đọc/ghi DB, huỷ chờ và trạng thái giao dịch."""

import re
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import main as app_main
from core.bank_service import BankService
from main import NodeRuntime
from test_lock_flow import Cluster, FakeAppWindow, FakeBully, FakeRoot, FakeServer, wait_until


class TransactionDelayTest(unittest.TestCase):
    def setUp(self):
        for target in ("main.log_event", "core.bank_service.log_event"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = 0.0
        self.writes = []
        self.conn = mock.MagicMock()
        cursor = self.conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1000,)

        def execute(sql, *args):
            if sql.startswith(("UPDATE", "INSERT")):
                self.writes.append((sql, self.clock))

        cursor.execute.side_effect = execute
        bank = BankService(database_url="test-only")
        bank._conn = self.conn
        server = FakeServer(0, Cluster())
        self.runtime = NodeRuntime(FakeRoot(), 0, {}, server,
                                   FakeAppWindow(None, 0), bank,
                                   FakeBully(server, 0, 0))
        self.runtime.release_lock = mock.Mock()
        self.runtime.refresh_balance_async = mock.Mock()
        self.runtime._broadcast_balance_changed = mock.Mock()

    def advance(self, seconds):
        self.clock += seconds
        return False

    def test_deposit_and_withdraw_count_down_before_any_db_write(self):
        for kind, delay in (("DEPOSIT", 3), ("WITHDRAW", 3), ("DEPOSIT", 5)):
            with self.subTest(kind=kind, delay=delay):
                self.runtime.transaction_delay = delay
                self.clock = 0.0
                self.writes.clear()
                self.runtime.app.events.clear()
                self.runtime._transaction_running = True
                with mock.patch.object(app_main, "time", SimpleNamespace(monotonic=lambda: self.clock)), \
                        mock.patch.object(self.runtime._stop_event, "wait", side_effect=self.advance):
                    self.runtime._do_transaction(kind, 100)
                counts = [int(match.group(1)) for event, value in self.runtime.app.events
                          if event == "transaction_status"
                          for match in [re.search(r"còn (\d+) giây", value)] if match]
                self.assertEqual(list(range(delay, -1, -1)), counts)
                self.assertEqual(2, len(self.writes))
                self.assertTrue(all(at == delay for _, at in self.writes))
                self.assertIn(("transaction_busy", False), self.runtime.app.events)
                self.assertFalse(self.runtime._transaction_running)

    def test_zero_delay_writes_without_waiting(self):
        self.runtime.transaction_delay = 0
        with mock.patch.object(self.runtime._stop_event, "wait") as wait:
            self.runtime._do_transaction("DEPOSIT", 100)
        wait.assert_not_called()
        self.assertEqual(2, len(self.writes))

    def test_shutdown_during_countdown_rolls_back_before_writing_and_releases(self):
        def stop(seconds):
            self.runtime._stop_event.set()
            return True

        with mock.patch.object(self.runtime._stop_event, "wait", side_effect=stop):
            self.runtime._do_transaction("DEPOSIT", 100)
        self.assertEqual([], self.writes)
        self.conn.rollback.assert_called_once()
        self.runtime.release_lock.assert_called_once()
        self.runtime._broadcast_balance_changed.assert_not_called()
        self.assertIn(("transaction_busy", False), self.runtime.app.events)

    def test_insufficient_balance_does_not_start_countdown(self):
        with mock.patch.object(self.runtime._stop_event, "wait") as wait:
            self.runtime._do_transaction("WITHDRAW", 2000)
        wait.assert_not_called()
        self.assertEqual([], self.writes)
        self.runtime.release_lock.assert_called_once()
        self.runtime._broadcast_balance_changed.assert_not_called()

    def test_delay_keeps_permission_while_other_node_can_queue(self):
        bank = self.runtime.bank
        bank.request_lock(0)
        entered = threading.Event()
        finish = threading.Event()
        self.addCleanup(finish.set)

        def pause(*args):
            entered.set()
            if not finish.wait(3):
                raise RuntimeError("Test chờ quá hạn")

        with mock.patch.object(self.runtime, "_wait_transaction_delay", side_effect=pause):
            worker = threading.Thread(target=self.runtime._do_transaction, args=("DEPOSIT", 100))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual([], self.writes)
                # Lời gọi này phải xong khi giao dịch vẫn đang chờ, không kẹt mutex.
                with mock.patch.object(bank, "_connect", side_effect=AssertionError("Không được vào DB")):
                    self.assertEqual("QUEUED", bank.request_lock(1))
                self.assertEqual(0, bank._lock_holder)
                self.assertEqual([1], bank._lock_queue)
            finally:
                finish.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())


class RuntimeConcurrentRequestsTest(unittest.TestCase):
    def test_repeated_grant_during_running_transaction_does_not_start_second_worker(self):
        cluster = Cluster()
        self.addCleanup(cluster.stop)
        with mock.patch("main.log_event"), mock.patch("core.bank_service.log_event"):
            coordinator = cluster.add(5, 5)
            node = cluster.add(3, 5)
            gate = threading.Event()
            self.addCleanup(gate.set)
            node.bank.gate = gate
            node.app.on_deposit(100)
            node.dispatch({"type": "GRANT", "from": 5}, 5)
            self.assertEqual([], node.bank.tx)
            gate.set()
            self.assertTrue(wait_until(lambda: len(node.bank.tx) == 1
                                      and coordinator.bank._lock_holder is None))

    def test_simultaneous_clicks_serialize_transactions_and_show_queue(self):
        cluster = Cluster()
        self.addCleanup(cluster.stop)
        with mock.patch("main.log_event"), mock.patch("core.bank_service.log_event"):
            coordinator = cluster.add(5, 5)
            first = cluster.add(3, 5)
            second = cluster.add(4, 5)
            gate = threading.Event()
            self.addCleanup(gate.set)
            first.bank.gate = gate
            second.bank.gate = gate
            barrier = threading.Barrier(2)

            def click(node, action):
                barrier.wait(timeout=3)
                action(100)

            workers = [threading.Thread(target=click, args=(first, first.app.on_deposit)),
                       threading.Thread(target=click, args=(second, second.app.on_withdraw))]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(3)
                self.assertFalse(worker.is_alive())
            try:
                self.assertEqual(1, len(coordinator.bank._lock_queue))
                waiting_id = coordinator.bank._lock_queue[0]
                waiting = cluster.nodes[waiting_id]
                self.assertEqual([], first.bank.tx)
                self.assertEqual([], second.bank.tx)
                self.assertIn(("transaction_status", "Đang xếp hàng chờ quyền giao dịch…"),
                              waiting.app.events)
            finally:
                gate.set()
            self.assertTrue(wait_until(lambda: len(first.bank.tx) == len(second.bank.tx) == 1
                                      and coordinator.bank._lock_holder is None))

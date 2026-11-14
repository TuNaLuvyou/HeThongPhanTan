"""Kiểm tra đồng bộ số dư, không mở UI và không kết nối DB thật."""

import socket
import threading
import unittest
from unittest import mock

import core.bank_service as bank_module
from core.bank_service import BankService
from core.network import ClientConnection, NetworkServer
from main import NodeRuntime
from test_lock_flow import (
    CONFIG, Cluster, FakeAppWindow, FakeBank, FakeBully, FakeRoot,
    FakeServer, _Node, wait_until,
)


class BalanceSyncTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("main.log_event")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cluster = Cluster()
        self.runtimes = {}
        # Tạo runtime trực tiếp để test không để lại các vòng periodic nền.
        for node_id in (3, 4, 5):
            server = FakeServer(node_id, self.cluster)
            app = FakeAppWindow(FakeRoot(), node_id)
            bank = FakeBank()
            bully = FakeBully(server, node_id, coordinator=5)
            runtime = NodeRuntime(FakeRoot(), node_id, CONFIG, server, app, bank, bully)
            server.set_message_handler(runtime.on_message)
            self.runtimes[node_id] = runtime
            self.cluster.nodes[node_id] = _Node(node_id, server, app, bank, bully)

    def test_successful_deposit_and_withdraw_refresh_other_nodes_after_release(self):
        sender = self.runtimes[3]
        coordinator = self.runtimes[5]
        for kind, method, new_balance in (
            ("DEPOSIT", "deposit", 1100), ("WITHDRAW", "withdraw", 900),
        ):
            with self.subTest(kind=kind):
                coordinator.bank._lock_holder = 3
                receiver = self.runtimes[4]
                receiver.app.events.clear()
                sender.server.sent.clear()
                original_send = sender.server.send

                def send(peer_id, payload):
                    if payload["type"] == "BALANCE_CHANGED":
                        self.assertIsNone(coordinator.bank._lock_holder)
                    return original_send(peer_id, payload)

                with mock.patch.object(sender.bank, method, return_value=True), \
                        mock.patch.object(receiver.bank, "get_balance", return_value=new_balance), \
                        mock.patch.object(sender.server, "send", side_effect=send):
                    sender._do_transaction(kind, 100)
                    self.assertTrue(wait_until(
                        lambda: ("balance", f"{new_balance:,}") in receiver.app.events
                    ))

                targets = [peer for peer, payload in sender.server.sent
                           if payload["type"] == "BALANCE_CHANGED"]
                self.assertEqual([4, 5, 6], targets)
                self.assertEqual([], receiver.server.sent)  # Không phát lại thông báo.

    def test_rejected_or_failed_transaction_does_not_broadcast(self):
        sender = self.runtimes[3]
        for result in (False, RuntimeError("Lỗi DB giả lập")):
            with self.subTest(result=result):
                sender.server.sent.clear()
                self.runtimes[5].bank._lock_holder = 3
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with mock.patch.object(sender.bank, "withdraw", **kwargs):
                    sender._do_transaction("WITHDRAW", 999999)
                self.assertFalse(any(payload["type"] == "BALANCE_CHANGED"
                                     for _, payload in sender.server.sent))
                self.assertIsNone(self.runtimes[5].bank._lock_holder)

    def test_unreachable_peer_does_not_prevent_notification_to_remaining_nodes(self):
        sender = self.runtimes[3]
        sender.server.down_peers.add(4)
        sender._broadcast_balance_changed()
        self.assertTrue(wait_until(lambda: any(
            "đang cập nhật số dư" in str(value)
            for kind, value in self.runtimes[5].app.events if kind == "log"
        )))

    def test_unknown_sender_cannot_trigger_refresh(self):
        receiver = self.runtimes[4]
        with mock.patch.object(receiver, "refresh_balance_async") as refresh:
            receiver.on_message(4, {"type": "BALANCE_CHANGED", "from": 99}, 99, None)
            refresh.assert_not_called()

    def test_notification_retries_connection_once(self):
        sender = self.runtimes[3]
        sender.config = {"nodes": [{"id": 3}, {"id": 4}]}
        with mock.patch.object(sender.server, "send", side_effect=[False, True]) as send, \
                mock.patch.object(sender.server, "connect_to_peer", return_value=True) as connect:
            sender._broadcast_balance_changed()
        connect.assert_called_once_with(4)
        self.assertEqual(2, send.call_count)


class BalanceReadTest(unittest.TestCase):
    def test_ui_read_uses_separate_connection_and_closes_it(self):
        bank = BankService(database_url="postgresql://test-only")
        write_connection = mock.Mock()
        bank._conn = write_connection
        read_connection = mock.MagicMock()
        read_connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1234,)
        with mock.patch.object(bank_module.psycopg2, "connect", return_value=read_connection):
            self.assertEqual(1234, bank.get_balance())
        read_connection.close.assert_called_once()
        write_connection.assert_not_called()
        self.assertIs(bank._conn, write_connection)


class IdleConnectionTest(unittest.TestCase):
    def test_stopping_server_closes_idle_incoming_connection(self):
        reader, writer = socket.socketpair()
        self.addCleanup(writer.close)
        conn = ClientConnection(("local", 0), timeout=0.02)
        conn.attach(reader)
        server = NetworkServer(0, "127.0.0.1", 0)
        server._running = True
        server._connections.add(conn)
        self.addCleanup(server.stop)
        worker = threading.Thread(target=server._listen_loop, args=(None, conn), daemon=True)
        worker.start()
        server.stop()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(set(), server._connections)

    def test_message_is_received_after_idle_timeout(self):
        reader, writer = socket.socketpair()
        conn = ClientConnection(("local", 0), timeout=0.02)
        conn.attach(reader)
        self.addCleanup(conn.close)
        self.addCleanup(writer.close)
        received = []
        started = threading.Event()

        def receive():
            started.set()
            received.append(conn.receive())

        worker = threading.Thread(target=receive, daemon=True)
        worker.start()
        self.assertTrue(started.wait(1))
        worker.join(0.1)  # Vượt nhiều chu kỳ timeout trước khi gửi.
        self.assertTrue(worker.is_alive())
        writer.sendall(b'{"type":"BALANCE_CHANGED","from":3}\n')
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual([{"type": "BALANCE_CHANGED", "from": 3}], received)


if __name__ == "__main__":
    unittest.main()

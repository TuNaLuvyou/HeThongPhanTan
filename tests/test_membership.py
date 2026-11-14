"""Gia nhập qua TCP thật, mỗi node chỉ có cấu hình của chính mình."""

import unittest
import socket
from unittest import mock

from core.bully import Bully
from core.network import NetworkServer
from main import NodeRuntime
from test_lock_flow import FakeAppWindow, FakeBank, FakeRoot, wait_until


class MembershipTest(unittest.TestCase):
    def setUp(self):
        for target, value in (
            ("core.bully.ELECTION_ANSWER_TIMEOUT", 0.15),
            ("core.bully.HEARTBEAT_INTERVAL", 0.05),
            ("core.bully.COORDINATOR_SILENCE_TIMEOUT", 0.3),
            ("main.NETWORK_RETRY_INTERVAL", 0.05),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ("core.network.log_event", "core.bully.log_event",
                       "core.bank_service.log_event", "main.log_event"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Runtime nhận cấu hình sẵn; Network và Bully không tự đọc lại file.
        patcher = mock.patch("core.network.load_config", side_effect=AssertionError("Không quét config"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def node(self, node_id, port=0):
        server = NetworkServer(node_id, "127.0.0.1", port, timeout=0.05)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        bully = Bully(server, node_id)
        self.addCleanup(bully.stop)
        runtime = NodeRuntime(FakeRoot(), node_id, {"nodes": [{"id": node_id}]},
                              server, FakeAppWindow(None, node_id), FakeBank(), bully)
        server.set_message_handler(runtime.on_message)
        bully.start()
        self.assertTrue(bully.is_coordinator())
        self.addCleanup(runtime._stop_event.set)
        return runtime

    def configure(self, runtime, nodes):
        runtime.config = {"nodes": [
            {"id": node.node_id, "ip": node.server.host, "port": node.server.port}
            for node in nodes
        ]}

    def test_configured_nodes_connect_automatically_without_join_argument(self):
        first = self.node(0)
        second = self.node(2)
        for runtime in (first, second):
            self.configure(runtime, (first, second))
            runtime.start()
        self.assertTrue(wait_until(lambda: first.bully.coordinator_id == 2
                                  and second.bully.coordinator_id == 2
                                  and first.server.is_connected(2)
                                  and second.server.is_connected(0), timeout=3))
        self.assertTrue(wait_until(lambda: any("Đã kết nối với nút 2" in line
                                              for line in first.app.logs()), timeout=3))

    def test_node_opened_later_is_found_by_background_retry(self):
        first = self.node(0)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            late_port = reservation.getsockname()[1]
        first.config = {"nodes": [
            {"id": 0, "ip": "127.0.0.1", "port": first.server.port},
            {"id": 2, "ip": "127.0.0.1", "port": late_port},
        ]}
        with mock.patch.object(first.server, "connect_configured_peer",
                               wraps=first.server.connect_configured_peer) as connect:
            first.start()
            self.assertTrue(wait_until(lambda: connect.called, timeout=1))
            self.assertEqual(0, first.bully.coordinator_id)
            late = self.node(2, late_port)
            self.assertTrue(wait_until(lambda: first.bully.coordinator_id == 2
                                      and late.server.is_connected(0), timeout=3))

    def join(self, newcomer, seed):
        self.assertTrue(newcomer.server.join("127.0.0.1", seed.server.port))

    def test_single_node_runs_without_peers(self):
        node = self.node(0)
        self.assertEqual([], node.server.peer_ids())
        self.assertEqual(0, node.bully.coordinator_id)

    def test_higher_id_joins_and_old_coordinator_clears_lock(self):
        old = self.node(0)
        old.bank._lock_holder = 0
        old.bank._lock_queue = [1]
        new = self.node(2)
        self.join(new, old)
        self.assertTrue(wait_until(lambda: old.bully.coordinator_id == 2
                                  and old.bank._lock_holder is None, timeout=3))
        self.assertEqual([], old.bank._lock_queue)
        self.assertEqual([2], old.server.peer_ids())
        self.assertEqual([0], new.server.peer_ids())
        self.assertTrue(wait_until(lambda: any(
            f"[Mạng] Đã kết nối với nút 2 (127.0.0.1:{new.server.port})" == message
            for message in old.app.logs()
        ), timeout=3))
        self.assertTrue(wait_until(lambda: any(
            f"[Mạng] Đã kết nối với nút 0 (127.0.0.1:{old.server.port})" == message
            for message in new.app.logs()
        ), timeout=3))

    def test_lower_id_joins_and_follows_existing_coordinator(self):
        high = self.node(5)
        low = self.node(0)
        self.join(low, high)
        self.assertTrue(wait_until(lambda: low.bully.coordinator_id == 5, timeout=3))
        self.assertTrue(high.bully.is_coordinator())

    def test_join_through_follower_discovers_mesh_and_survives_coordinator_loss(self):
        first = self.node(1)
        middle = self.node(3)
        self.join(middle, first)
        self.assertTrue(wait_until(lambda: first.bully.coordinator_id == 3, timeout=3))
        high = self.node(5)
        self.join(high, first)
        self.assertTrue(wait_until(lambda: all(n.bully.coordinator_id == 5
                                              for n in (first, middle, high)), timeout=3))
        first._pending_type = "DEPOSIT"
        first._pending_amount = 100
        first._last_coordinator_id = 5
        low = self.node(0)
        self.join(low, first)  # Địa chỉ gia nhập không cần là coordinator.
        nodes = (first, middle, high, low)
        self.assertTrue(wait_until(lambda: all(len(n.server.peer_ids()) == 3
                                              and n.bully.coordinator_id == 5 for n in nodes), timeout=3))
        self.assertEqual("DEPOSIT", first._pending_type, first.app.logs())
        high.bully.stop()
        high.server.stop()
        self.assertTrue(wait_until(lambda: all(n.bully.coordinator_id == 3
                                              for n in (first, middle, low)), timeout=4))

    def test_send_reconnects_to_discovered_member(self):
        first = self.node(0)
        second = self.node(2)
        self.join(second, first)
        self.assertTrue(wait_until(lambda: first.bully.coordinator_id == 2, timeout=3))
        with first.server._lock:
            old = first.server._clients.pop(2)
        old.close()
        self.assertTrue(first.server.send(2, {"type": "HEARTBEAT_ACK", "from": 0}))


if __name__ == "__main__":
    unittest.main()

"""Kiểm tra thứ tự kết nối/bầu chọn và đường gửi ngược cho node mở sau."""

import socket
import threading
import unittest
from unittest import mock

from core.network import ClientConnection, NetworkServer
from main import NodeRuntime


class StartupTest(unittest.TestCase):
    def make_runtime(self):
        runtime = NodeRuntime(
            mock.Mock(), 0, {"nodes": [{"id": 0}, {"id": 2}, {"id": 5}]},
            mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(),
        )
        runtime._ui_log = mock.Mock()
        runtime.update_status = mock.Mock()
        runtime.refresh_balance_async = mock.Mock()
        runtime._periodic_update = mock.Mock()
        self.addCleanup(runtime._stop_event.set)
        return runtime

    def test_all_connections_finish_before_bully_starts_without_blocking_ui(self):
        runtime = self.make_runtime()
        entered = threading.Event()
        release = threading.Event()
        elected = threading.Event()
        finished = []
        self.addCleanup(release.set)

        def connect(peer_id):
            entered.set()
            release.wait(2)
            finished.append(peer_id)
            return peer_id == 2  # Node 5 offline vẫn phải hoàn tất lần thử.

        runtime.server.connect_to_peer.side_effect = connect
        runtime.bully.start.side_effect = elected.set
        runtime.start()
        self.assertTrue(entered.wait(1))
        self.assertFalse(elected.is_set())
        self.assertTrue(callable(runtime.app.on_deposit))
        release.set()
        self.assertTrue(elected.wait(2))
        self.assertEqual([2, 5], sorted(finished))
        runtime.bully.start.assert_called_once()

    def test_close_while_connecting_does_not_restart_bully(self):
        runtime = self.make_runtime()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def connect(peer_id):
            entered.set()
            release.wait(2)
            return True

        runtime.server.connect_to_peer.side_effect = connect
        worker = threading.Thread(target=runtime._connect_peers)
        worker.start()
        self.assertTrue(entered.wait(1))
        runtime.shutdown()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        runtime.bully.start.assert_not_called()
        runtime.bully.stop.assert_called_once()

    def test_failed_connection_does_not_abort_election(self):
        runtime = self.make_runtime()
        runtime.server.connect_to_peer.side_effect = OSError("Máy chưa mở")
        runtime._connect_peers()
        runtime.bully.start.assert_called_once()


class IncomingRouteTest(unittest.TestCase):
    def test_existing_node_can_send_to_late_node_on_incoming_socket(self):
        reader, writer = socket.socketpair()
        incoming = ClientConnection(("local", 0), timeout=0.02)
        incoming.attach(reader)
        late_node = ClientConnection(("local", 0), timeout=0.02)
        late_node.attach(writer)
        self.addCleanup(late_node.close)
        server = NetworkServer(5, "127.0.0.1", 0)
        server._running = True
        server._connections.add(incoming)
        handled = threading.Event()
        server.set_message_handler(lambda *args: handled.set())
        self.addCleanup(server.stop)
        worker = threading.Thread(target=server._listen_loop, args=(None, incoming), daemon=True)
        worker.start()
        late_node.send({"type": "ELECTION", "from": 2})
        self.assertTrue(handled.wait(1))
        self.assertTrue(server.send(2, {"type": "COORDINATOR", "from": 5}))
        received = []
        reply = threading.Thread(target=lambda: received.append(late_node.receive()), daemon=True)
        reply.start()
        reply.join(1)
        self.assertFalse(reply.is_alive())
        self.assertEqual([{"type": "COORDINATOR", "from": 5}], received)
        server.stop()
        worker.join(1)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()

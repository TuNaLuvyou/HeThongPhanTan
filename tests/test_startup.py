"""Bully chạy ngay, TCP tự kết nối theo cấu hình và không chặn UI."""

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

    def test_bully_starts_before_background_connections_finish(self):
        runtime = self.make_runtime()
        elected = threading.Event()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        runtime.server.is_connected.return_value = False

        def connect(node_config):
            self.assertTrue(elected.is_set())
            entered.set()
            release.wait(2)
            return False

        runtime.server.connect_configured_peer.side_effect = connect
        runtime.bully.start.side_effect = elected.set
        runtime.start()
        self.assertTrue(elected.wait(1))
        self.assertTrue(entered.wait(1))
        self.assertTrue(callable(runtime.app.on_deposit))
        runtime.bully.start.assert_called_once()
        release.set()

    def test_close_during_background_connection_stops_retry(self):
        runtime = self.make_runtime()
        runtime.server.is_connected.return_value = False
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def connect(node_config):
            entered.set()
            release.wait(2)
            return False

        runtime.server.connect_configured_peer.side_effect = connect
        worker = threading.Thread(target=runtime._maintain_peer_connection, args=({"id": 2},))
        worker.start()
        self.assertTrue(entered.wait(1))
        runtime.shutdown()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        runtime.server.connect_configured_peer.assert_called_once_with({"id": 2})
        runtime.bully.start.assert_not_called()
        runtime.bully.stop.assert_called_once()

    def test_offline_peer_is_retried_without_restarting_bully(self):
        runtime = self.make_runtime()
        runtime.server.is_connected.return_value = False
        runtime.server.connect_configured_peer.side_effect = [False, True]
        with mock.patch.object(runtime._stop_event, "wait", side_effect=[False, True]):
            runtime._maintain_peer_connection({"id": 2})
        self.assertEqual(2, runtime.server.connect_configured_peer.call_count)
        runtime.bully.start.assert_not_called()

    def test_connected_peer_does_not_receive_repeated_handshakes(self):
        runtime = self.make_runtime()
        runtime.server.is_connected.return_value = True
        with mock.patch.object(runtime._stop_event, "wait", return_value=True):
            runtime._maintain_peer_connection({"id": 2})
        runtime.server.connect_configured_peer.assert_not_called()

    def test_closed_node_does_not_start_services(self):
        runtime = self.make_runtime()
        runtime.shutdown()
        runtime._start_services()
        runtime.bully.start.assert_not_called()


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

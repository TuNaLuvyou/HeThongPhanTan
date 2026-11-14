"""Kiểm tra khởi động độc lập và đường gửi ngược cho node mở sau."""

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

    def test_starts_without_contacting_configured_nodes(self):
        runtime = self.make_runtime()
        elected = threading.Event()
        runtime.bully.start.side_effect = elected.set
        runtime.start()
        self.assertTrue(elected.wait(1))
        self.assertTrue(callable(runtime.app.on_deposit))
        runtime.server.connect_to_peer.assert_not_called()
        runtime.server.join.assert_not_called()
        runtime.bully.start.assert_called_once()

    def test_join_does_not_delay_local_election_and_shutdown_stops_retry(self):
        runtime = self.make_runtime()
        runtime.join_address = ("127.0.0.1", 3002)
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def join(host, port):
            runtime.bully.start.assert_called_once()
            entered.set()
            release.wait(2)
            return False

        runtime.server.join.side_effect = join
        runtime.start()
        self.assertTrue(entered.wait(1))
        runtime.shutdown()
        release.set()
        runtime.bully.start.assert_called_once()
        runtime.bully.stop.assert_called_once()

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

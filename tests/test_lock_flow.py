# tests/test_lock_flow.py - Kiem thu dong Request/Grant/Release va cach main.py phan ung khi doi coordinator.
# Dung that main.py (khong mo cua so, khong cham DB): Tkinter, Bully, Bank va Network deu duoc gia lap.
# Chay: python -m unittest discover -s tests

import threading
import time
import unittest
from unittest import mock

import main as app_main
from core.bank_service import BankService

NODE_IDS = (3, 4, 5, 6)  # 6 dung cho kich ban bau lai coordinator trong test doi coordinator
CONFIG = {"shared_balance_initial": 1000, "nodes": [{"id": i, "ip": "127.0.0.1", "port": 3000 + i} for i in NODE_IDS]}


class FakeBank(BankService):
    """BankService that rieng logic hang doi khoa, bo qua DB."""

    def __init__(self):
        BankService.__init__(self, database_url="postgresql://khong-co-that")
        self.tx = []
        self.gate = None
        self.fail = False

    def _connect(self):
        raise AssertionError("Test khong duoc phep cham DB")

    def deposit(self, node_id, amount):
        self._record("DEPOSIT", node_id, amount)
        return not self.fail

    def withdraw(self, node_id, amount):
        self._record("WITHDRAW", node_id, amount)
        return not self.fail

    def _record(self, kind, node_id, amount):
        if self.gate is not None:
            self.gate.wait(5)
        self.tx.append((kind, node_id, amount))

    def get_balance(self):
        return 1000

    def close(self):
        pass


class FakeRoot:
    def protocol(self, name, callback):
        self.on_close = callback

    def mainloop(self):
        pass


class FakeTk:
    def Tk(self):
        return FakeRoot()


class FakeAppWindow:
    def __init__(self, root, node_id):
        self.node_id = node_id
        self.events = []
        self.on_deposit = None
        self.on_withdraw = None

    def post_event(self, kind, payload=None):
        self.events.append((kind, payload))

    def logs(self):
        return [payload for kind, payload in self.events if kind == "log"]


class FakeBully:
    """Bully that chi dong vai tro va hanh vi ha/len coordinator."""

    def __init__(self, server, node_id, coordinator):
        self.node_id = node_id
        self.coordinator_id = coordinator
        self.elections = []
        self.server = server

    def start(self):
        return True

    def stop(self):
        pass

    def is_coordinator(self):
        return self.coordinator_id == self.node_id

    def trigger_election(self, reason=""):
        self.elections.append(reason)

    def handle_message(self, node_id, message, peer_id, conn):
        if message.get("type") == "COORDINATOR" and peer_id is not None:
            self.coordinator_id = int(peer_id)


class FakeServer:
    def __init__(self, node_id, cluster):
        self.node_id = node_id
        self.cluster = cluster
        self.sent = []
        self.handler = None
        self.down_peers = set()

    def set_message_handler(self, handler):
        self.handler = handler

    def send(self, peer_id, payload):
        self.sent.append((peer_id, payload))
        if peer_id in self.down_peers:
            return False
        peer = self.cluster.nodes.get(peer_id)
        if peer is not None and peer.server.handler is not None:
            peer.server.handler(peer_id, payload, self.node_id, None)
        return True

    def connect_to_peer(self, peer_id):
        return True

    def stop(self):
        pass


class Cluster:
    def __init__(self):
        self.nodes = {}

    def add(self, node_id, coordinator, bully_factory=None):
        server = FakeServer(node_id, self)
        app_holder = {}

        def make_app(root, nid):
            app = FakeAppWindow(root, nid)
            app_holder["app"] = app
            return app

        bank = FakeBank()
        bully = (bully_factory or (lambda srv, nid: FakeBully(srv, nid, coordinator)))(server, node_id)

        with mock.patch.multiple(
            app_main,
            load_config=lambda path="config.json": CONFIG,
            start_node=lambda nid: server,
            tk=FakeTk(),
            AppWindow=make_app,
            Bully=lambda srv, nid: bully,
            BankService=lambda: bank,
            log_event=lambda *args, **kwargs: None,
        ):
            with mock.patch.object(app_main.sys, "argv", ["main.py", "--id", str(node_id)]):
                app_main.main()

        node = _Node(node_id, server, app_holder["app"], bank, bully)
        self.nodes[node_id] = node
        return node


class _Node:
    def __init__(self, node_id, server, app, bank, bully):
        self.node_id = node_id
        self.server = server
        self.app = app
        self.bank = bank
        self.bully = bully

    def dispatch(self, payload, from_id):
        """Gui mot goi tin den node nay nhu the NetworkServer vua nhan duoc."""
        self.server.handler(self.node_id, payload, from_id, None)


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class LockFlowTest(unittest.TestCase):
    def setUp(self):
        # DONG thoi gian cho de check_pending_timeout chay duoc trong test.
        patcher = mock.patch.object(app_main, "LOCK_REPLY_TIMEOUT", 0.1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_coordinator_releases_its_own_lock(self):
        """Hoi truoc day: coordinator bam Nap xong thi khoa bi giu mai, 5 node con lai ket tran."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)

        coordinator.app.on_deposit(50000)
        self.assertTrue(wait_until(lambda: coordinator.bank.tx == [("DEPOSIT", 5, 50000)]))
        self.assertIsNone(coordinator.bank._lock_holder)

        # Node khac phai xin duoc quyen sau do, khong bi nam trong hang doi vo tan.
        coordinator.dispatch({"type": "REQUEST_LOCK", "from": 3}, 3)
        self.assertIn((3, {"type": "GRANT_LOCK", "from": 5}), coordinator.server.sent)

    def test_lock_moves_between_two_nodes(self):
        """Node 3 nap tien qua coordinator 5: REQUEST -> GRANT -> TX -> RELEASE -> khoa rong."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)
        node3 = cluster.add(3, coordinator=5)

        node3.app.on_deposit(1000)
        self.assertTrue(wait_until(lambda: node3.bank.tx == [("DEPOSIT", 3, 1000)]))
        self.assertIsNone(coordinator.bank._lock_holder)
        self.assertEqual([], coordinator.bank.tx)

    def test_second_node_waits_in_fifo_queue(self):
        """Node 4 xin quyen khi node 3 dang giu: nhan LOCK_QUEUED, roi duoc GRANT_LOCK theo thu tu."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)
        node3 = cluster.add(3, coordinator=5)
        node4 = cluster.add(4, coordinator=5)

        gate = threading.Event()
        node3.bank.gate = gate
        node3.app.on_deposit(100)
        self.assertTrue(wait_until(lambda: coordinator.bank._lock_holder == 3))

        node4.app.on_deposit(200)
        self.assertTrue(wait_until(lambda: coordinator.bank._lock_queue == [4]))
        self.assertIn((4, {"type": "LOCK_QUEUED", "from": 5}), coordinator.server.sent)

        gate.set()
        self.assertTrue(wait_until(lambda: node4.bank.tx == [("DEPOSIT", 4, 200)]))
        self.assertEqual([("DEPOSIT", 3, 100)], node3.bank.tx)
        self.assertIsNone(coordinator.bank._lock_holder)
        self.assertEqual([], coordinator.bank._lock_queue)

    def test_failed_transaction_still_releases_lock(self):
        """Rut vuot so du bi tu choi van phai tra khoa (FLOWS muc 5)."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)
        node3 = cluster.add(3, coordinator=5)
        node3.bank.fail = True

        node3.app.on_withdraw(999999)
        self.assertTrue(wait_until(lambda: node3.bank.tx == [("WITHDRAW", 3, 999999)]))
        self.assertIsNone(coordinator.bank._lock_holder)

        coordinator.dispatch({"type": "REQUEST_LOCK", "from": 4}, 4)
        self.assertIn((4, {"type": "GRANT_LOCK", "from": 5}), coordinator.server.sent)

    def test_demoted_coordinator_clears_lock_state(self):
        """FLOWS muc 3: mat vai tro coordinator thi hang doi cu phai bi xoa sach."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)
        coordinator.bank._lock_holder = 3
        coordinator.bank._lock_queue = [4]

        # Node 6 (ID lon hon) vua cong bo minh la coordinator moi.
        coordinator.dispatch({"type": "COORDINATOR", "from": 6}, 6)

        self.assertFalse(coordinator.bully.is_coordinator())
        self.assertIsNone(coordinator.bank._lock_holder)
        self.assertEqual([], coordinator.bank._lock_queue)

    def test_pending_request_cancelled_on_coordinator_change(self):
        """Don xin quyen bi vo hieu khi doi coordinator thi phai huy de user bam lai duoc."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)
        node3 = cluster.add(3, coordinator=5)
        # Phai de vong cap nhat trang thai ghi lai coordinator hien tai truoc khi doi.
        self.assertTrue(wait_until(lambda: ("coordinator", "5") in node3.app.events))
        # Node 4 dang giu khoa (gia lap bang tay) nen don cua node 3 chi xep hang, khong chay.
        coordinator.bank._lock_holder = 4

        node3.app.on_deposit(100)
        self.assertTrue(wait_until(lambda: coordinator.bank._lock_queue == [3]))

        node3.bully.coordinator_id = 6  # mang vua bau lai coordinator khac
        self.assertTrue(
            wait_until(lambda: any("Huy giao dich dang cho" in line for line in node3.app.logs()), timeout=8.0)
        )
        self.assertEqual([], node3.bank.tx)

    def test_pending_sent_to_new_coordinator_not_cancelled(self):
        """Bam Nap ngay sau khi bau coordinator moi, truoc vong periodic ke tiep:
        don gui toi coordinator moi khong duoc huy oan nhu don cu cua coordinator cu."""
        cluster = Cluster()
        cluster.add(5, coordinator=5)
        coordinator6 = cluster.add(6, coordinator=6)
        node3 = cluster.add(3, coordinator=5)
        self.assertTrue(wait_until(lambda: ("coordinator", "5") in node3.app.events))

        node3.bully.coordinator_id = 6  # bau lai xong nhung periodic chua chay tiep
        coordinator6.bank._lock_holder = 9  # node khac dang giu quyen -> don cua 3 xep hang
        node3.app.on_deposit(100)
        self.assertTrue(wait_until(lambda: coordinator6.bank._lock_queue == [3]))

        # Cho chac chan it nhat mot vong periodic chay xong voi coordinator moi.
        self.assertTrue(wait_until(lambda: ("coordinator", "6") in node3.app.events))
        self.assertFalse(any("Huy giao dich dang cho" in line for line in node3.app.logs()))

        coordinator6.dispatch({"type": "RELEASE_LOCK", "from": 9}, 9)
        self.assertTrue(wait_until(lambda: node3.bank.tx == [("DEPOSIT", 3, 100)]))

    def test_orphan_grant_releases_lock_immediately(self):
        """Coordinator cap quyen ma node khong con don cho (don bi huy) thi node phai tra ngay,
        neu khong coordinator ghi oan node do van giu quyen va FIFO ket tran vo han."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)
        node3 = cluster.add(3, coordinator=5)
        coordinator.bank._lock_holder = 3
        coordinator.bank._lock_queue = [4]

        node3.dispatch({"type": "GRANT_LOCK", "from": 5}, 5)

        self.assertIn((5, {"type": "RELEASE_LOCK", "from": 3}), node3.server.sent)
        # RELEASE that quay ve coordinator nen hang doi duoc tiep tuc phuc vu.
        self.assertEqual(4, coordinator.bank._lock_holder)
        self.assertIn((4, {"type": "GRANT_LOCK", "from": 5}), coordinator.server.sent)

    def test_unreachable_coordinator_triggers_election(self):
        """Gui REQUEST_LOCK that bai (coordinator da tat) thi phai bau lai, khong choi im lang."""
        cluster = Cluster()
        cluster.add(5, coordinator=5)
        node3 = cluster.add(3, coordinator=5)
        node3.server.down_peers.add(5)

        node3.app.on_deposit(100)
        self.assertEqual(["REQUEST_LOCK_SEND_FAIL"], node3.bully.elections)

    def test_silent_coordinator_triggers_election(self):
        """FLOWS muc 2b: REQUEST_LOCK khong co phan hoi thi bau lai."""
        cluster = Cluster()
        coordinator = cluster.add(5, coordinator=5)
        node3 = cluster.add(3, coordinator=5)
        # Co lien ket nhung khong bao gio tra loi: cat handler cua coordinator.
        coordinator.server.handler = None

        node3.app.on_deposit(100)
        self.assertTrue(wait_until(lambda: "REQUEST_LOCK_NO_REPLY" in node3.bully.elections, timeout=8.0))
        self.assertTrue(any("khong phan hoi" in line for line in node3.app.logs()))


if __name__ == "__main__":
    unittest.main()

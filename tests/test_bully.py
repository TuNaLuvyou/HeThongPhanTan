# tests/test_bully.py - Kiem thu giai thuat Bully tren mesh gia lap (khong dung socket).
# Chay: python -m unittest discover -s tests

import time
import unittest
from unittest import mock

import core.bully as bully_module
from core.bully import Bully


class FakeConn:
    """Ket noi gia lap: message gui tren nay di toi node `dst` cua mesh."""

    def __init__(self, mesh, src, dst):
        self.mesh = mesh
        self.src = src
        self.dst = dst

    def send(self, payload):
        return self.mesh.deliver(self.dst, payload, self.src)


class FakeNode:
    def __init__(self, node_id, mesh):
        self.node_id = node_id
        self.mesh = mesh
        self.online = True
        self.bully = None

    def send_to(self, peer_id, payload):
        if not self.online:
            return False
        return self.mesh.deliver(peer_id, payload, self.node_id)

    def receive(self, payload, from_id):
        if self.bully is None:
            return
        #Nhu NetworkServer that: peer_id lay tu truong `from` cua goi tin (TECHS muc 5).
        conn = FakeConn(self.mesh, self.node_id, from_id)
        self.bully.handle_message(self.node_id, payload, payload.get("from"), conn)


class _ServerShim:
    """Doi tuong thay cho NetworkServer trong test."""

    def __init__(self, node):
        self.node = node

    def send(self, peer_id, payload):
        return self.node.send_to(peer_id, payload)


class FakeMesh:
    def __init__(self, node_ids):
        self.nodes = {nid: FakeNode(nid, self) for nid in node_ids}
        for node in self.nodes.values():
            node.bully = Bully(_ServerShim(node), node.node_id)

    def deliver(self, dst_id, payload, from_id):
        dst = self.nodes.get(dst_id)
        if dst is None or not dst.online:
            return False
        dst.receive(payload, from_id)
        return True


class BullyTest(unittest.TestCase):
    def setUp(self):
        self.logs = []
        patches = [
            mock.patch.object(bully_module, "load_config", lambda: {"nodes": [{"id": i} for i in (0, 1, 2)]}),
            mock.patch.object(
                bully_module, "log_event", lambda nid, event, detail="": self.logs.append((nid, event, detail))
            ),
            mock.patch.object(bully_module, "ELECTION_ANSWER_TIMEOUT", 0.2),
            mock.patch.object(bully_module, "HEARTBEAT_INTERVAL", 0.05),
            mock.patch.object(bully_module, "COORDINATOR_SILENCE_TIMEOUT", 0.3),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def events(self, name):
        return [row for row in self.logs if row[1] == name]

    def converge(self, mesh):
        """Bat dau bau cua o moi node dang chay, nhu luc both may cung khoi dong."""
        for node in mesh.nodes.values():
            if node.online:
                node.bully._start_election("TEST")

    # ------------------------------------------------------------------ BAU CU

    def test_highest_alive_node_becomes_coordinator(self):
        mesh = FakeMesh((0, 1, 2))
        self.converge(mesh)
        self.assertTrue(mesh.nodes[2].bully.is_coordinator())
        for nid in (0, 1):
            self.assertEqual("IDLE", mesh.nodes[nid].bully.state)
            self.assertEqual(2, mesh.nodes[nid].bully.coordinator_id)
        self.assertEqual(1, len([nid for nid in (0, 1, 2) if mesh.nodes[nid].bully.is_coordinator()]))

    def test_offline_higher_node_does_not_win(self):
        mesh = FakeMesh((0, 1, 2))
        mesh.nodes[2].online = False
        self.converge(mesh)
        self.assertTrue(mesh.nodes[1].bully.is_coordinator())
        self.assertEqual(1, mesh.nodes[0].bully.coordinator_id)

    def test_coordinator_steps_down_when_higher_node_joins(self):
        """FLOWS muc 3: node ID cao hon bat len thi coordinator cu ha vai tro."""
        mesh = FakeMesh((0, 1, 2))
        mesh.nodes[2].online = False
        self.converge(mesh)
        self.assertTrue(mesh.nodes[1].bully.is_coordinator())

        mesh.nodes[2].online = True
        mesh.nodes[2].bully._start_election("JOIN")

        self.assertTrue(mesh.nodes[2].bully.is_coordinator())
        self.assertFalse(mesh.nodes[1].bully.is_coordinator())
        self.assertEqual(2, mesh.nodes[1].bully.coordinator_id)
        stepped_down = [(nid, detail) for nid, _, detail in self.events("STEP_DOWN")]
        self.assertEqual([(1, "coordinator=2")], stepped_down)

    def test_coordinator_survives_election_from_lower_id(self):
        """Node ID thap hon bau lai khong duoc lam phat vai tro cua coordinator dang tai."""
        mesh = FakeMesh((0, 1, 2))
        self.converge(mesh)
        self.logs.clear()
        mesh.nodes[2].bully.handle_message(
            2, {"type": "ELECTION", "from": 0}, 0, FakeConn(mesh, 2, 0)
        )
        self.assertTrue(mesh.nodes[2].bully.is_coordinator())
        self.assertEqual([], self.events("STEP_DOWN"))
        self.assertEqual(2, mesh.nodes[0].bully.coordinator_id)

    def test_higher_id_node_answers_election(self):
        """Node cao hon phai tra ANSWER, neu khong node khoi dong tu phong minh la coordinator."""
        mesh = FakeMesh((0, 1, 2))
        mesh.nodes[0].bully._start_election("TEST")
        self.assertFalse(mesh.nodes[0].bully.is_coordinator())
        self.assertEqual("IDLE", mesh.nodes[0].bully.state)
        self.assertTrue(mesh.nodes[2].bully.is_coordinator())

    def test_answer_from_lower_id_is_ignored(self):
        mesh = FakeMesh((0, 1, 2))
        bully2 = mesh.nodes[2].bully
        bully2.state = "ELECTING"
        bully2.handle_message(2, {"type": "ANSWER", "from": 1}, 1, FakeConn(mesh, 2, 1))
        self.assertEqual({}, bully2._election_answers)

    def test_election_does_not_start_twice_at_once(self):
        mesh = FakeMesh((0, 1, 2))
        bully1 = mesh.nodes[1].bully
        bully1.state = "ELECTING"
        before = len(self.logs)
        bully1._start_election("TEST")
        self.assertEqual(before, len(self.logs))

    # ---------------------------------------------------------------- HEARTBEAT

    def test_coordinator_heartbeats_and_tracks_peers(self):
        mesh = FakeMesh((0, 1, 2))
        self.converge(mesh)
        self.logs.clear()
        mesh.nodes[2].bully._send_heartbeats()
        alive = self.events("PEERS_ALIVE")
        self.assertEqual(1, len(alive))
        self.assertIn("alive=[0,1]", alive[0][2])

    def test_silent_peer_does_not_make_coordinator_reelect(self):
        """Peer khong phan hoi chi la node offline, khong phai coordinator loi."""
        mesh = FakeMesh((0, 1, 2))
        self.converge(mesh)
        mesh.nodes[0].online = False
        before = len(self.events("ELECTION_START"))
        mesh.nodes[2].bully._send_heartbeats()
        mesh.nodes[2].bully._watch_coordinator()
        self.assertTrue(mesh.nodes[2].bully.is_coordinator())
        self.assertEqual(before, len(self.events("ELECTION_START")))

    def test_coordinator_reasserts_itself_to_lower_node(self):
        """Node ID thap hon bau lai thi coordinator tra COORDINATOR cho dung lai, tranh hai coordinator."""
        mesh = FakeMesh((1, 2))
        self.converge(mesh)
        follower = mesh.nodes[1].bully
        follower.state = "ELECTING"  # dang nghi coordinator chet nen tu bau lai
        follower.coordinator_id = None

        mesh.nodes[2].bully.handle_message(2, {"type": "ELECTION", "from": 1}, 1, FakeConn(mesh, 2, 1))

        self.assertEqual("IDLE", follower.state)
        self.assertEqual(2, follower.coordinator_id)
        self.assertTrue(mesh.nodes[2].bully.is_coordinator())

    def test_follower_detects_silent_coordinator(self):
        """FLOWS muc 2a: coordinator im lang qua thoi gian thi node phu bau lai."""
        mesh = FakeMesh((1, 2))
        self.converge(mesh)
        follower = mesh.nodes[1].bully
        self.assertEqual(2, follower.coordinator_id)
        self.logs.clear()

        mesh.nodes[2].online = False
        follower._last_coordinator_seen = time.monotonic() - 5
        follower._watch_coordinator()

        self.assertTrue(follower.is_coordinator())
        self.assertEqual(1, self.events("COORDINATOR_FAIL")[0][0])

    def test_heartbeat_from_coordinator_postpones_reelection(self):
        mesh = FakeMesh((1, 2))
        self.converge(mesh)
        follower = mesh.nodes[1].bully
        follower._last_coordinator_seen = time.monotonic() - 5
        follower.handle_message(1, {"type": "HEARTBEAT", "from": 2}, 2, FakeConn(mesh, 1, 2))
        # ACK phai di duoc mot vong ve coordinator, va node phu phai ngung dem thoi gian im lang.
        self.assertIsNotNone(mesh.nodes[2].bully._last_heartbeat_ack.get(1))
        follower._watch_coordinator()
        self.assertFalse(follower.is_coordinator())

    def test_no_coordinator_yet_triggers_reelection(self):
        """Node da yield nhung node thang chet truoc khi kip cong bo -> khong duoc ket máy."""
        mesh = FakeMesh((0, 1, 2))
        # Node 1 va 2 offline thi node 0 moi hop le tu phong minh khi khong co ANSWER nao.
        mesh.nodes[1].online = False
        mesh.nodes[2].online = False
        follower = mesh.nodes[0].bully
        follower.state = "IDLE"
        follower.coordinator_id = None
        follower._last_coordinator_seen = time.monotonic() - 5
        follower._watch_coordinator()
        self.assertTrue(follower.is_coordinator())
        self.assertEqual(1, len(self.events("NO_COORDINATOR")))

    def test_watchdog_thread_keeps_heartbeating_in_background(self):
        """Hoi truoc day vong canh thoat ngay khi chua la coordinator nen khong phat hien duoc loi nao.

        O nay ca hai node deu start(): node 2 gui HEARTBEAT dinh ky, node 1 ACK va khong bau lai.
        """
        mesh = FakeMesh((1, 2))
        for node in mesh.nodes.values():
            node.bully.start()
            self.addCleanup(node.bully.stop)
        time.sleep(0.3)

        coordinators = [nid for nid in (1, 2) if mesh.nodes[nid].bully.is_coordinator()]
        self.assertEqual([2], coordinators)
        self.assertTrue(mesh.nodes[1].bully._loop_thread.is_alive())
        self.assertEqual(2, mesh.nodes[1].bully.coordinator_id)
        # Node phai van gui duoc tin nhan (HEARTBEAT_ACK) sau 0.3s vong canh chay nen.
        self.assertIsNotNone(mesh.nodes[2].bully._last_heartbeat_ack.get(1))

    def test_stopped_node_does_not_take_roles_again(self):
        mesh = FakeMesh((1, 2))
        follower = mesh.nodes[1].bully
        follower.start()
        # start() hoi tu binh thuong va da ghi FOLLOW_COORDINATOR; xoa log de chi kiem tra sau stop.
        self.logs.clear()
        follower.stop()
        follower.handle_message(1, {"type": "COORDINATOR", "from": 2}, 2, FakeConn(mesh, 1, 2))
        self.assertEqual([], self.events("FOLLOW_COORDINATOR"))
        self.assertEqual("IDLE", follower.state)
        # Node da roi mang thi khong duoc tu khoi dong bau cua lai.
        follower.trigger_election("MANUAL")
        self.assertEqual([], self.events("ELECTION_START"))
        self.assertTrue(self.events("IGNORE_AFTER_STOP"))


if __name__ == "__main__":
    unittest.main()

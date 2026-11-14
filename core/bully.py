# core/bully.py - Thanh vien 2 (macOS ARM) - Giai thuat Bully: ELECTION, ANSWER, COORDINATOR, heartbeat, phat hien coordinator loi
# Doc tham chieu: docs/TECHS.md (muc 2, 6, 8), docs/FLOWS.md (muc 1, 2, 3), docs/BUSINESS.md

import threading
import time
from typing import Optional

from core.network import (
    ClientConnection,
    NetworkServer,
    load_config,
    log_event,
)

ELECTION = "ELECTION"
ANSWER = "ANSWER"
COORDINATOR = "COORDINATOR"
HEARTBEAT = "HEARTBEAT"
HEARTBEAT_ACK = "HEARTBEAT_ACK"

# Cac gia tri thoi gian duoi day thuoc muc 9b trong docs/TECHS.md (CẦN CHỐT).
ELECTION_ANSWER_TIMEOUT = 5.0  # Thoi gian toi da cho ANSWER tu node co ID lon hon.
HEARTBEAT_INTERVAL = 2.0  # Chu ky gui HEARTBEAT va chu ky kiem tra cua vong canh.
HEARTBEAT_TIMEOUT = 3.0  # Ngan nay khong co HEARTBEAT_ACK thi coi peer da offline (chi de ghi log).
COORDINATOR_SILENCE_TIMEOUT = 6.0  # Ngan nay khong co dau hieu coordinator con song thi bau lai.


class Bully:
    """Giai thuat Bully: id lon hon thang.

    - Node co ID lon nhat con song giu vai tro coordinator.
    - Coordinator gui HEARTBEAT dinh ky de cac node khac biet minh con song.
    - Node phu phat hien coordinator loi khi coordinator im lang qua lau (FLOWS muc 2a)
      roi tu bat dau bau cua lai.

    Interface de module khac goi: start, stop, is_coordinator, trigger_election,
    handle_message, coordinator_id, state.
    """

    def __init__(self, server: NetworkServer, node_id: int):
        self.server = server
        self.node_id = node_id
        self.coordinator_id: Optional[int] = None
        self.state = "IDLE"  # IDLE | ELECTING | COORDINATOR
        self._election_answers: dict = {}
        self._last_heartbeat_ack: dict = {}
        self._liveness_logged = ""
        self._election_lock = threading.Lock()
        self._loop_thread: Optional[threading.Thread] = None
        self._running = False
        self._stopped = False
        # Thoi diem gan nhat thay dau hieu coordinator con song (HEARTBEAT hoac COORDINATOR).
        self._last_coordinator_seen = time.monotonic()

    # ------------------------------------------------------------------ API

    def start(self) -> bool:
        """Bat dau lop Bully: chay vong canh va bau chon coordinator dau tien."""
        if self._running:
            return True
        self._running = True
        self._stopped = False
        self._loop_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._loop_thread.start()
        self._start_election("STARTUP")
        return True

    def stop(self) -> None:
        """Dung vong canh va danh dau node da chu dong roi mang."""
        self._running = False
        self._stopped = True
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=HEARTBEAT_INTERVAL)

    def is_coordinator(self) -> bool:
        return self.state == "COORDINATOR"

    def trigger_election(self, reason: str = "MANUAL") -> None:
        """Module khac (main.py) goi khi phat hien coordinator khong phan hoi."""
        self._start_election(reason)

    # ------------------------------------------------------------- BAU CU

    def _peer_ids(self) -> list:
        """Danh sach ID cac node (khong phai node hien tai) trong cau hinh."""
        peer_ids = []
        for node in load_config().get("nodes", []):
            peer_id = int(node.get("id"))
            if peer_id != self.node_id:
                peer_ids.append(peer_id)
        return peer_ids

    def _start_election(self, reason: str = "") -> None:
        """Chay thuong qua bau cua Bully: gui ELECTION cho node ID lon hon roi xem ANSWER.

        Quy tac (FLOWS muc 1.5 va 2.3):
        - Co node ID lon hon tra loi ANSWER -> dung lai, cho node do cong bo COORDINATOR.
        - Khong co ANSWER nao trong ELECTION_ANSWER_TIMEOUT -> chinh minh la coordinator.
        """
        if self._stopped:
            # User da chu dong tat cua so thi node coi như roi mang, khong duoc gianh lai vai tro.
            log_event(self.node_id, "IGNORE_AFTER_STOP", f"type=ELECTION reason={reason}")
            return
        with self._election_lock:
            if self.state in ("ELECTING", "COORDINATOR"):
                # Da co cuoc bau dang chay, hoac minh dang la coordinator ID cao nhat.
                return
            self.state = "ELECTING"
            self.coordinator_id = None
            self._election_answers = {}
        self._last_coordinator_seen = time.monotonic()
        log_event(self.node_id, "ELECTION_START", f"from_node={self.node_id} reason={reason}")

        higher_ids = sorted(p for p in self._peer_ids() if p > self.node_id)
        for peer_id in higher_ids:
            sent = self.server.send(peer_id, {"type": ELECTION, "from": self.node_id})
            if not sent:
                log_event(self.node_id, "ELECTION_SEND_FAIL", f"peer={peer_id}")

        deadline = time.monotonic() + ELECTION_ANSWER_TIMEOUT
        yielded_to = None
        while True:
            with self._election_lock:
                if self.state != "ELECTING":
                    # Co node khac da cong bo COORDINATOR trong luc minh cho.
                    return
                answered_ids = sorted(pid for pid, ok in self._election_answers.items() if ok)
                if answered_ids:
                    self.state = "IDLE"
                    self._last_coordinator_seen = time.monotonic()
                    yielded_to = answered_ids
            if yielded_to is not None:
                log_event(self.node_id, "ELECTION_YIELD", f"higher_alive={yielded_to}")
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)

        with self._election_lock:
            if self.state != "ELECTING":
                return
            self.coordinator_id = self.node_id
            self.state = "COORDINATOR"
            self._last_coordinator_seen = time.monotonic()
            self._last_heartbeat_ack = {}
        log_event(self.node_id, "BECOME_COORDINATOR", f"node={self.node_id}")
        self._broadcast_coordinator()

    def _broadcast_coordinator(self) -> None:
        """Cong bo minh la coordinator toi tat ca cac node khac."""
        for peer_id in self._peer_ids():
            self.server.send(peer_id, {"type": COORDINATOR, "from": self.node_id})

    # ------------------------------------------------------- VONG CANH

    def _main_loop(self) -> None:
        """Vong canh chay lien tuc: coordinator thi goi heartbeat, node phu thi theo doi coordinator.

        Vong nay phai chay o moi vai tro, neu khong node phu khong the phat hien coordinator loi.
        """
        while self._running:
            if self.state == "COORDINATOR":
                self._send_heartbeats()
            elif self.state != "ELECTING":
                self._watch_coordinator()
            time.sleep(HEARTBEAT_INTERVAL)

    def _send_heartbeats(self) -> None:
        """Coordinator gui HEARTBEAT toi tung peer de chung minh minh con song."""
        for peer_id in self._peer_ids():
            # Peer khong phan hoi chi co nghia la node do offline, khong phai loi coordinator,
            # nen khong duoc phep bat bau cua tu day.
            self.server.send(peer_id, {"type": HEARTBEAT, "from": self.node_id})
        self._log_liveness()

    def _log_liveness(self) -> None:
        """Ghi danh sach peer con phan hoi heartbeat, chi ghi khi danh sach thay doi."""
        now = time.monotonic()
        alive = sorted(
            pid for pid in self._peer_ids() if now - self._last_heartbeat_ack.get(pid, 0.0) <= HEARTBEAT_TIMEOUT
        )
        snapshot = ",".join(str(pid) for pid in alive)
        if snapshot != self._liveness_logged:
            self._liveness_logged = snapshot
            log_event(self.node_id, "PEERS_ALIVE", f"coordinator={self.node_id} alive=[{snapshot}]")

    def _watch_coordinator(self) -> None:
        """Node phu phat hien coordinator loi theo FLOWS muc 2a: im lang qua COORDINATOR_SILENCE_TIMEOUT."""
        if self.state == "COORDINATOR":
            return
        if time.monotonic() - self._last_coordinator_seen <= COORDINATOR_SILENCE_TIMEOUT:
            return
        if self.coordinator_id is not None:
            log_event(self.node_id, "COORDINATOR_FAIL", f"coordinator={self.coordinator_id} silent")
        else:
            log_event(self.node_id, "NO_COORDINATOR", "yielded but no COORDINATOR announced")
        self._start_election("WATCHDOG")

    # --------------------------------------------------------- HANDLER

    def handle_message(
        self, node_id: int, message: dict, peer_id: Optional[int], conn: ClientConnection
    ) -> None:
        """Nhan message tu NetworkServer -> dispatch theo type."""
        msg_type = message.get("type")
        sender_id = peer_id if peer_id is not None else message.get("from")
        if sender_id is not None:
            sender_id = int(sender_id)

        if msg_type == ELECTION:
            self._on_election(sender_id, conn)
        elif msg_type == ANSWER:
            self._on_answer(sender_id)
        elif msg_type == COORDINATOR:
            self._on_coordinator_announce(sender_id)
        elif msg_type == HEARTBEAT:
            self._on_heartbeat(sender_id, conn)
        elif msg_type == HEARTBEAT_ACK:
            self._on_heartbeat_ack(sender_id)

    def _on_election(self, sender_id: Optional[int], conn: ClientConnection) -> None:
        """Xu ly ELECTION theo quy tac Bully: chi node co ID LON HON moi tra ANSWER.

        Node khoi dong bao gio cung gui ELECTION len cac node ID lon hon minh, va chi can
        biet trong so do co ai con song. Vi vay:
        - Minh la node cao hon (sender < minh): tra ANSWER de node kia chiu dung lai,
          roi chinh minh bat dau bau cua tiep (FLOWS muc 2.3).
        - Minh la coordinator: tra them COORDINATOR de node kia theo lai ngay, neu khong
          no se tu phong minh va hai node cung la coordinator.
        - Sender la node cao hon minh: khong co gi can tra loi.
        """
        if sender_id is None or sender_id >= self.node_id:
            return
        conn.send({"type": ANSWER, "from": self.node_id, "to": sender_id})
        if self.state == "COORDINATOR":
            conn.send({"type": COORDINATOR, "from": self.node_id})
            return
        self._start_election(f"ELECTION_FROM_{sender_id}")

    def _on_answer(self, sender_id: Optional[int]) -> None:
        """Ghi nhan node ID lon hon con song va dang trong tieu bau cua minh."""
        if sender_id is None or sender_id <= self.node_id:
            return
        with self._election_lock:
            if self.state == "ELECTING":
                self._election_answers[sender_id] = True

    def _on_coordinator_announce(self, sender_id: Optional[int]) -> None:
        """Nhan cong bo COORDINATOR: chi theo node co ID lon hon minh (ID lon hon thang)."""
        if sender_id is None or sender_id <= self.node_id:
            return
        if self._stopped:
            # Node da tat window van con goi tin roi; khong duoc khoi dong lai vai tro sau khi stop.
            log_event(self.node_id, "IGNORE_AFTER_STOP", f"type=COORDINATOR from={sender_id}")
            return
        was_coordinator = self.state == "COORDINATOR"
        with self._election_lock:
            self.coordinator_id = sender_id
            self.state = "IDLE"
            self._last_coordinator_seen = time.monotonic()
        event = "STEP_DOWN" if was_coordinator else "FOLLOW_COORDINATOR"
        log_event(self.node_id, event, f"coordinator={sender_id}")

    def _on_heartbeat(self, sender_id: Optional[int], conn: ClientConnection) -> None:
        """Node phu tra HEARTBEAT_ACK va ghi nhan coordinator con song."""
        if sender_id is None or self.state == "COORDINATOR":
            return
        self._last_coordinator_seen = time.monotonic()
        conn.send({"type": HEARTBEAT_ACK, "from": self.node_id, "to": sender_id})

    def _on_heartbeat_ack(self, sender_id: Optional[int]) -> None:
        """Coordinator ghi nhan peer con phan hoi heartbeat."""
        if sender_id is None or self.state != "COORDINATOR":
            return
        self._last_heartbeat_ack[sender_id] = time.monotonic()

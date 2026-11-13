# core/bully.py - Thanh vien 2 (macOS ARM) - Giai thuat Bully: ELECTION, ANSWER, COORDINATOR, heartbeat, phat hien coordinator loi
# Doc tham chieu: docs/TECHS.md (muc 2, 6, 8), docs/FLOWS.md, docs/BUSINESS.md

import threading
import time
from typing import Optional

from core.network import (
    ClientConnection,
    NetworkServer,
    get_host_by_id,
    get_port_by_id,
    load_config,
    log_event,
)

ELECTION = "ELECTION"
ANSWER = "ANSWER"
COORDINATOR = "COORDINATOR"
HEARTBEAT = "HEARTBEAT"
HEARTBEAT_ACK = "HEARTBEAT_ACK"

ELECTION_ANSWER_TIMEOUT = 5.0  # Thoi gian cho phe nhan cua node lon hon khi bat dau bau cua minh.
HEARTBEAT_INTERVAL = 2.0  # Chu ky gui heartbeat cua coordinator.
HEARTBEAT_TIMEOUT = 3.0  # Thoi gian doi heartbeat (HEARTBEAT_ACK) de phat hien coordinator loi.


class Bully:
    """Giai thuat Bully: id lon hon thang.

    - Nguoi co ID lon nhat (con song) se giam vai tro la coordinator.
    - Coordinator dat giao dien cho cac giao dich (via bank_service o module sau).
    - Khi coordinator loi (heartbeat khong duoc ack), he thong tu bau coordinator moi.
    """

    def __init__(self, server: NetworkServer, node_id: int):
        self.server = server
        self.node_id = node_id
        self.coordinator_id: Optional[int] = None
        self.state = "IDLE"  # IDLE | ELECTING | COORDINATOR
        self._election_answers: dict = {}
        self._last_heartbeat_ack: dict = {}
        self._election_lock = threading.Lock()
        self._heartbeat_stop_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------ API

    def start(self) -> bool:
        """Bat dau cai lop Bully: chay vong heartbeat + bau chon coordinator."""
        self._running = True
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        self._start_election()
        return True

    def stop(self) -> None:
        """Dung vong heartbeat cua Bully."""
        self._running = False
        self._heartbeat_stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2)

    def is_coordinator(self) -> bool:
        return self.state == "COORDINATOR"

    # ------------------------------------------------------------- BAU CU

    def _peer_ids(self) -> list:
        """Danh sach ID cac node (khong phai node hien tai) trong cau hinh."""
        peer_ids = []
        for node in load_config().get("nodes", []):
            peer_id = int(node.get("id"))
            if peer_id != self.node_id:
                peer_ids.append(peer_id)
        return peer_ids

    def _start_election(self) -> None:
        """Chay thuong qua bau cua Bully: gui ELECTION, doi ANSWER."""
        if self.state == "COORDINATOR":
            return
        self.state = "ELECTING"
        self._election_answers = {}
        log_event(self.node_id, "ELECTION_START", f"from_node={self.node_id}")

        higher_ids = sorted(p for p in self._peer_ids() if p > self.node_id)
        for peer_id in higher_ids:
            sent = self.server.send(peer_id, {"type": ELECTION, "from": self.node_id})
            if not sent:
                log_event(self.node_id, "ELECTION_SEND_FAIL", f"peer={peer_id}")

        deadline = time.monotonic() + ELECTION_ANSWER_TIMEOUT
        while time.monotonic() < deadline and self.state == "ELECTING":
            with self._election_lock:
                answered_ids = [pid for pid, ok in self._election_answers.items() if ok]
            if answered_ids:
                winner_id = max(answered_ids)
                self.coordinator_id = winner_id
                self.state = "IDLE"
                log_event(self.node_id, "ELECTION_DONE", f"coordinator={winner_id}")
                return
            time.sleep(0.05)

        # Khong co node nao tra loi ANSWER -> toi lam coordinator.
        self.coordinator_id = self.node_id
        self.state = "COORDINATOR"
        log_event(self.node_id, "BECOME_COORDINATOR", f"node={self.node_id}")
        self._broadcast_coordinator()

    def _broadcast_coordinator(self) -> None:
        """Gui COORDINATOR toi tat ca cac node con song."""
        for peer_id in self._peer_ids():
            if peer_id == self.node_id:
                continue
            self.server.send(peer_id, {"type": COORDINATOR, "from": self.node_id})

    def _trigger_election(self) -> None:
        """Phat hien coordinator loi -> chay bau cua nhan."""
        if self.state == "COORDINATOR":
            log_event(self.node_id, "COORDINATOR_FAIL", f"coordinator={self.coordinator_id}")
            self.state = "IDLE"
            self.coordinator_id = None
            self._start_election()

    # --------------------------------------------------------- HEARTBEAT

    def _heartbeat_loop(self) -> None:
        """Vong heartbeat cua coordinator: gui HEARTBEAT, doi HEARTBEAT_ACK."""
        while self._running and self.state == "COORDINATOR":
            for peer_id in self._peer_ids():
                if peer_id == self.node_id:
                    continue
                if not self.server.send(peer_id, {"type": HEARTBEAT, "from": self.node_id}):
                    self._trigger_election()
                    return
            time.sleep(HEARTBEAT_INTERVAL)
            now = time.monotonic()
            for peer_id in self._peer_ids():
                if peer_id == self.node_id:
                    continue
                last_ack = self._last_heartbeat_ack.get(peer_id, 0.0)
                if last_ack and (now - last_ack) > HEARTBEAT_TIMEOUT:
                    self._trigger_election()
                    return

    # --------------------------------------------------------- HANDLER

    def handle_message(
        self, node_id: int, message: dict, peer_id: Optional[int], conn: ClientConnection
    ) -> None:
        """Nhan message tu NetworkServer -> dispatch theo type."""
        msg_type = message.get("type")
        sender_id = peer_id if peer_id is not None else message.get("from")

        if msg_type == ELECTION:
            # Node nhan ELECTION tu node lon hon -> tra ANSWER.
            if sender_id is not None and sender_id > self.node_id:
                conn.send({"type": ANSWER, "from": self.node_id, "to": sender_id})
        elif msg_type == ANSWER:
            # Ghi nhan cau tra loi cua cac node (dang trong tia bau cua minh).
            if self.state == "ELECTING" and sender_id is not None and sender_id > self.node_id:
                with self._election_lock:
                    self._election_answers[sender_id] = True
        elif msg_type == COORDINATOR:
            # Nhan duoc huong vai tro moi -> ve vai tro node binh thuong.
            if sender_id is not None and sender_id != self.node_id:
                self.coordinator_id = sender_id
                self.state = "IDLE"
                log_event(self.node_id, "FOLLOW_COORDINATOR", f"coordinator={sender_id}")
        elif msg_type == HEARTBEAT:
            # Heartbeat chi goi cho coordinator -> follower tra HEARTBEAT_ACK.
            if sender_id is not None and self.state != "COORDINATOR":
                conn.send({"type": HEARTBEAT_ACK, "from": self.node_id, "to": sender_id})
        elif msg_type == HEARTBEAT_ACK:
            # Coordinator ghi nhan heartbeat tu peer.
            if self.state == "COORDINATOR" and sender_id is not None:
                with self._election_lock:
                    self._last_heartbeat_ack[sender_id] = time.monotonic()

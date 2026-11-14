# core/bully.py - Thành viên 2 (macOS ARM)
"""Giải thuật Bully để bầu coordinator cho mạng 6 node.

Ý tưởng: node có ID lớn nhất còn sống sẽ là coordinator.

Mỗi node nằm ở một trong ba trạng thái:
    IDLE        - node bình thường, đang theo một coordinator.
    ELECTING    - đang chạy bầu chọn, chờ ANSWER từ các node ID lớn hơn.
    COORDINATOR - node này đang giữ vai trò coordinator.

Các gói tin Bully dùng trong file này:
    ELECTION      : node bắt đầu bầu cử gửi cho các node ID lớn hơn.
    ANSWER        : node ID lớn hơn trả lời "tao còn sống".
    COORDINATOR   : node thắng cử thông báo cho toàn mạng.
    HEARTBEAT     : coordinator gửi định kỳ để chứng minh còn sống.
    HEARTBEAT_ACK : node phụ trả lời heartbeat.

Tài liệu tham chiếu: docs/TECHS.md (mục 2, 6, 8), docs/FLOWS.md (mục 1, 2, 3).
"""

import threading
import time
from typing import Optional

from core.network import (
    ClientConnection,
    NetworkServer,
    load_config,
    log_event,
)

# Tên các loại gói tin Bully.
ELECTION = "ELECTION"
ANSWER = "ANSWER"
COORDINATOR = "COORDINATOR"
HEARTBEAT = "HEARTBEAT"
HEARTBEAT_ACK = "HEARTBEAT_ACK"

# ---------------------------------------------------------------------------
# Tham số thời gian (đơn vị: giây) - thuộc mục 9b trong docs/TECHS.md.
# ---------------------------------------------------------------------------

# Chờ tối đa bao lâu để nhận ANSWER từ các node ID lớn hơn.
ELECTION_ANSWER_TIMEOUT = 5.0

# Chu kỳ coordinator gửi HEARTBEAT và chu kỳ vòng canh kiểm tra.
HEARTBEAT_INTERVAL = 2.0

# Peer không trả HEARTBEAT_ACK quá lâu thì coi là offline (chỉ để ghi log).
HEARTBEAT_TIMEOUT = 3.0

# Coordinator im lặng quá lâu thì node phụ bầu lại (FLOWS mục 2a).
COORDINATOR_SILENCE_TIMEOUT = 6.0


class Bully:
    """Giải thuật Bully cho một node.

    Các hàm module khác được dùng:
        start()             - bật vòng canh và bầu coordinator lần đầu.
        stop()              - tắt vòng canh, không nhận vai trò nữa.
        is_coordinator()    - node này có đang là coordinator không.
        trigger_election()  - chủ động bầu lại khi phát hiện coordinator lỗi.
        handle_message()    - xử lý một gói tin Bully nhận từ mạng.
        coordinator_id      - ID coordinator hiện tại (None nếu chưa biết).
        state               - trạng thái hiện tại của node.
    """

    def __init__(self, server: NetworkServer, node_id: int):
        self.server = server
        self.node_id = node_id

        self.coordinator_id: Optional[int] = None
        self.state = "IDLE"  # IDLE | ELECTING | COORDINATOR

        # Các node ID lớn hơn đã trả ANSWER trong cuộc bầu cử đang chạy.
        self._election_answers: dict = {}

        # Thời điểm nhận HEARTBEAT_ACK gần nhất của từng peer.
        self._last_heartbeat_ack: dict = {}

        # Chuỗi peer còn sống lần trước, dùng để tránh ghi log lặp lại.
        self._liveness_logged = ""

        # Bảo vệ các biến trạng thái được nhiều thread cùng đọc/ghi.
        self._election_lock = threading.Lock()

        self._loop_thread: Optional[threading.Thread] = None
        self._running = False
        self._stopped = False

        # Lần gần nhất thấy dấu hiệu coordinator còn sống
        # (nhận HEARTBEAT hoặc COORDINATOR).
        self._last_coordinator_seen = time.monotonic()

    # ------------------------------------------------------------------
    # API cho các module khác
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Bật vòng canh nền và bắt đầu bầu coordinator lần đầu."""
        if self._running:
            return True

        self._running = True
        self._stopped = False

        self._loop_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._loop_thread.start()

        self._start_election("STARTUP")
        return True

    def stop(self) -> None:
        """Tắt vòng canh và đánh dấu node đã chủ động rời mạng."""
        self._running = False
        self._stopped = True

        if self._loop_thread is not None:
            self._loop_thread.join(timeout=HEARTBEAT_INTERVAL)

    def is_coordinator(self) -> bool:
        """Trả về True nếu node này đang là coordinator."""
        return self.state == "COORDINATOR"

    def trigger_election(self, reason: str = "MANUAL") -> None:
        """Bầu lại khi module khác phát hiện coordinator không phản hồi."""
        self._start_election(reason)

    # ------------------------------------------------------------------
    # Bầu chọn
    # ------------------------------------------------------------------

    def _peer_ids(self) -> list:
        """Trả về danh sách ID của các node khác trong config.json."""
        config = load_config()
        peer_ids = []
        for node_config in config.get("nodes", []):
            peer_id = int(node_config.get("id"))
            if peer_id != self.node_id:
                peer_ids.append(peer_id)
        return peer_ids

    def _start_election(self, reason: str = "") -> None:
        """Chạy một cuộc bầu cử theo giải thuật Bully.

        Quy tắc (FLOWS mục 1.5 và 2.3):
        - Gửi ELECTION cho mọi node có ID lớn hơn.
        - Nếu có node trả ANSWER: dừng lại, chờ node đó công bố COORDINATOR.
        - Nếu không ai trả lời trong ELECTION_ANSWER_TIMEOUT: tự làm coordinator.
        """
        if self._stopped:
            # Người dùng đã đóng cửa sổ: node coi như đã rời mạng.
            log_event(self.node_id, "IGNORE_AFTER_STOP", f"type=ELECTION reason={reason}")
            return

        with self._election_lock:
            if self.state in ("ELECTING", "COORDINATOR"):
                # Đang có cuộc bầu cử khác chạy, hoặc mình đã là coordinator.
                return
            self.state = "ELECTING"
            self.coordinator_id = None
            self._election_answers = {}

        self._last_coordinator_seen = time.monotonic()
        log_event(self.node_id, "ELECTION_START", f"from_node={self.node_id} reason={reason}")

        # Chỉ các node có ID lớn hơn mới có quyền trả lời.
        higher_peer_ids = []
        for peer_id in self._peer_ids():
            if peer_id > self.node_id:
                higher_peer_ids.append(peer_id)
        higher_peer_ids.sort()

        for peer_id in higher_peer_ids:
            payload = {"type": ELECTION, "from": self.node_id}
            if not self.server.send(peer_id, payload):
                log_event(self.node_id, "ELECTION_SEND_FAIL", f"peer={peer_id}")

        deadline = time.monotonic() + ELECTION_ANSWER_TIMEOUT
        answered_peer_ids = []

        while True:
            with self._election_lock:
                if self.state != "ELECTING":
                    # Một node khác đã công bố COORDINATOR trong lúc mình chờ.
                    return

                answered_peer_ids = []
                for peer_id, answered in self._election_answers.items():
                    if answered:
                        answered_peer_ids.append(peer_id)
                answered_peer_ids.sort()

                if answered_peer_ids:
                    self.state = "IDLE"
                    self._last_coordinator_seen = time.monotonic()

            if answered_peer_ids:
                log_event(self.node_id, "ELECTION_YIELD", f"higher_alive={answered_peer_ids}")
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
        """Thông báo cho tất cả các node biết mình là coordinator."""
        for peer_id in self._peer_ids():
            payload = {"type": COORDINATOR, "from": self.node_id}
            self.server.send(peer_id, payload)

    # ------------------------------------------------------------------
    # Vòng canh nền
    # ------------------------------------------------------------------

    def _main_loop(self) -> None:
        """Vòng lặp chạy suốt thời gian node hoạt động.

        - Nếu là coordinator: gửi HEARTBEAT định kỳ.
        - Nếu là node phụ: theo dõi xem coordinator còn sống không.
        - Nếu đang bầu cử: không làm gì, để cuộc bầu cử tự chạy.
        """
        while self._running:
            if self.state == "COORDINATOR":
                self._send_heartbeats()
            elif self.state != "ELECTING":
                self._watch_coordinator()

            time.sleep(HEARTBEAT_INTERVAL)

    def _send_heartbeats(self) -> None:
        """Coordinator gửi HEARTBEAT cho từng peer để chứng minh còn sống."""
        for peer_id in self._peer_ids():
            # Peer không trả lời chỉ có nghĩa node đó offline, không phải lỗi
            # của coordinator, nên không được bầu lại từ đây.
            payload = {"type": HEARTBEAT, "from": self.node_id}
            self.server.send(peer_id, payload)

        self._log_liveness()

    def _log_liveness(self) -> None:
        """Ghi log danh sách peer còn phản hồi heartbeat.

        Chỉ ghi khi danh sách thay đổi, tránh spam log mỗi 2 giây.
        """
        now = time.monotonic()
        alive_peer_ids = []

        for peer_id in self._peer_ids():
            last_ack = self._last_heartbeat_ack.get(peer_id, 0.0)
            if now - last_ack <= HEARTBEAT_TIMEOUT:
                alive_peer_ids.append(peer_id)

        alive_peer_ids.sort()
        snapshot = ",".join(str(peer_id) for peer_id in alive_peer_ids)

        if snapshot == self._liveness_logged:
            return

        self._liveness_logged = snapshot
        log_event(
            self.node_id,
            "PEERS_ALIVE",
            f"coordinator={self.node_id} alive=[{snapshot}]",
        )

    def _watch_coordinator(self) -> None:
        """Phát hiện coordinator chết vì im lặng quá lâu (FLOWS mục 2a)."""
        if self.state == "COORDINATOR":
            return

        silence_duration = time.monotonic() - self._last_coordinator_seen
        if silence_duration <= COORDINATOR_SILENCE_TIMEOUT:
            return

        if self.coordinator_id is not None:
            log_event(
                self.node_id,
                "COORDINATOR_FAIL",
                f"coordinator={self.coordinator_id} silent",
            )
        else:
            log_event(
                self.node_id,
                "NO_COORDINATOR",
                "yielded but no COORDINATOR announced",
            )

        self._start_election("WATCHDOG")

    # ------------------------------------------------------------------
    # Xử lý gói tin nhận từ mạng
    # ------------------------------------------------------------------

    def handle_message(
        self,
        node_id: int,
        message: dict,
        peer_id: Optional[int],
        conn: ClientConnection,
    ) -> None:
        """Nhận một gói tin từ NetworkServer và chuyển tới hàm xử lý phù hợp."""
        message_type = message.get("type")

        sender_id = peer_id
        if sender_id is None:
            sender_id = message.get("from")
        if sender_id is not None:
            sender_id = int(sender_id)

        if message_type == ELECTION:
            self._on_election(sender_id, conn)
        elif message_type == ANSWER:
            self._on_answer(sender_id)
        elif message_type == COORDINATOR:
            self._on_coordinator_announce(sender_id)
        elif message_type == HEARTBEAT:
            self._on_heartbeat(sender_id, conn)
        elif message_type == HEARTBEAT_ACK:
            self._on_heartbeat_ack(sender_id)

    def _on_election(self, sender_id: Optional[int], conn: ClientConnection) -> None:
        """Xử lý ELECTION theo luật Bully: chỉ node ID lớn hơn mới trả ANSWER.

        Ba trường hợp:
        - Node gửi có ID nhỏ hơn mình: trả ANSWER, rồi tự bầu cử tiếp.
          Nếu mình đang là coordinator thì gửi thêm COORDINATOR để node kia
          theo lại ngay, tránh hai coordinator cùng tồn tại.
        - Node gửi có ID lớn hơn mình: không cần trả lời.
        - Không xác định được node gửi: bỏ qua.
        """
        if sender_id is None or sender_id >= self.node_id:
            return

        conn.send({"type": ANSWER, "from": self.node_id, "to": sender_id})

        if self.state == "COORDINATOR":
            conn.send({"type": COORDINATOR, "from": self.node_id})
            return

        self._start_election(f"ELECTION_FROM_{sender_id}")

    def _on_answer(self, sender_id: Optional[int]) -> None:
        """Ghi nhận một node ID lớn hơn còn sống trong cuộc bầu cử hiện tại."""
        if sender_id is None or sender_id <= self.node_id:
            return

        with self._election_lock:
            if self.state == "ELECTING":
                self._election_answers[sender_id] = True

    def _on_coordinator_announce(self, sender_id: Optional[int]) -> None:
        """Nhận thông báo COORDINATOR từ node thắng cử.

        Chỉ chấp nhận node có ID lớn hơn mình (ID lớn hơn thắng).
        """
        if sender_id is None or sender_id <= self.node_id:
            return

        if self._stopped:
            # Node đã tắt cửa sổ nhưng vẫn còn gói tin tới; không nhận vai trò.
            log_event(
                self.node_id,
                "IGNORE_AFTER_STOP",
                f"type=COORDINATOR from={sender_id}",
            )
            return

        was_coordinator = self.state == "COORDINATOR"

        with self._election_lock:
            self.coordinator_id = sender_id
            self.state = "IDLE"
            self._last_coordinator_seen = time.monotonic()

        if was_coordinator:
            event = "STEP_DOWN"
        else:
            event = "FOLLOW_COORDINATOR"
        log_event(self.node_id, event, f"coordinator={sender_id}")

    def _on_heartbeat(self, sender_id: Optional[int], conn: ClientConnection) -> None:
        """Node phụ trả HEARTBEAT_ACK và ghi nhận coordinator còn sống."""
        if sender_id is None or self.state == "COORDINATOR":
            return

        self._last_coordinator_seen = time.monotonic()
        conn.send({"type": HEARTBEAT_ACK, "from": self.node_id, "to": sender_id})

    def _on_heartbeat_ack(self, sender_id: Optional[int]) -> None:
        """Coordinator ghi nhận một peer còn phản hồi heartbeat."""
        if sender_id is None or self.state != "COORDINATOR":
            return

        self._last_heartbeat_ack[sender_id] = time.monotonic()

# main.py - Trưởng nhóm (macOS ARM)
"""Điểm khởi động của một node trong Hệ thống Quỹ chung Phân tán.

File này ghép bốn module lại với nhau:
    ui/app_window.py     - cửa sổ Tkinter
    core/network.py      - gửi/nhận gói tin TCP
    core/bully.py        - bầu chọn coordinator
    core/bank_service.py - số dư trong DB và khoá loại trừ tương hỗ

Trình tự khởi động:
    1. Đọc tham số --id và tìm cấu hình node trong config.json.
    2. Mở server TCP (bind port theo cấu hình).
    3. Mở cửa sổ Tkinter.
    4. Bật Bully ngay, tự kết nối các địa chỉ cấu hình trong nền.
    5. Chờ người dùng bấm Nạp/Rút.

Cách chạy:
    python main.py --id 0
"""

import argparse
import json
import math
import sys
import threading
import time
import tkinter as tk

from core.bank_service import BankService
from core.bully import Bully
from core.network import log_event, start_node
from ui.app_window import AppWindow

# Các loại gói tin do Bully xử lý (xem docs/TECHS.md mục 6).
BULLY_MESSAGE_TYPES = (
    "ELECTION",
    "ANSWER",
    "COORDINATOR",
    "HEARTBEAT",
    "HEARTBEAT_ACK",
)

# Thời gian chờ coordinator phản hồi sau khi gửi REQUEST.
# Quá hạn này thì coi coordinator đã lỗi (docs/FLOWS.md mục 2b).
REQUEST_REPLY_TIMEOUT = 10.0

# Chu kỳ của vòng cập nhật trạng thái nền.
STATUS_UPDATE_INTERVAL = 2.0

# Độ trễ mô phỏng sau khi đọc số dư, trước khi ghi; đặt 0 để tắt.
TRANSACTION_DELAY = 3.0

# Peer chưa mở hoặc vừa ngắt kết nối được thử lại độc lập trong nền.
NETWORK_RETRY_INTERVAL = 5.0


def load_config(path: str = "config.json") -> dict:
    """Đọc file cấu hình chung của dự án."""
    with open(path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def find_node_config(config: dict, node_id: int):
    """Tìm phần cấu hình của node theo id. Trả về None nếu không có."""
    for node_config in config.get("nodes", []):
        if node_config.get("id") == node_id:
            return node_config
    return None


class NodeRuntime:
    """Ghép UI, mạng, Bully và ngân hàng cho một node.

    Mọi trạng thái của luồng Nạp/Rút được giữ ở đây thay vì nằm trong các
    biến closure rải rác, để dễ đọc và dễ theo dõi khi debug.

    Trạng thái đơn xin quyền (phía node xin quyền):
        _pending_type    : "DEPOSIT" | "WITHDRAW" | None
        _pending_amount  : số tiền của đơn đang chờ
        _pending_sent_at : thời điểm gửi REQUEST (time.monotonic()).
                           None nghĩa là coordinator đã phản hồi.
    """

    def __init__(
        self,
        root: tk.Tk,
        node_id: int,
        config: dict,
        server,
        app: AppWindow,
        bank: BankService,
        bully: Bully,
        transaction_delay=None,
    ):
        self.root = root
        self.node_id = node_id
        self.config = config
        self.server = server
        self.app = app
        self.bank = bank
        self.bully = bully
        self.transaction_delay = max(0.0, float(
            TRANSACTION_DELAY if transaction_delay is None else transaction_delay
        ))

        # Bảo vệ ba biến _pending_* vì chúng bị nhiều thread cùng đọc/ghi.
        self._pending_lock = threading.Lock()
        self._balance_refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pending_type = None
        self._pending_amount = 0
        self._pending_sent_at = None
        self._transaction_running = False

        # Coordinator gần nhất mà node này biết, dùng để phát hiện đổi
        # coordinator giữa chừng.
        self._last_coordinator_id = None

    # ------------------------------------------------------------------
    # Khởi động và tắt
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Bật Bully ngay; tự kết nối TCP theo cấu hình trong nền."""
        self.app.on_deposit = lambda amount: self.request_transaction("DEPOSIT", amount)
        self.app.on_withdraw = lambda amount: self.request_transaction("WITHDRAW", amount)

        self.server.set_message_handler(self.on_message)

        startup_thread = threading.Thread(target=self._start_services, daemon=True)
        startup_thread.start()

        periodic_thread = threading.Thread(target=self._periodic_update, daemon=True)
        periodic_thread.start()

    def shutdown(self) -> None:
        """Dừng mọi dịch vụ và đóng cửa sổ."""
        self._stop_event.set()
        self._ui_log("Đang dừng nút...")

        try:
            self.bully.stop()
            self.server.stop()
            self.bank.close()
        except Exception as exc:
            log_event(self.node_id, "STOP_ERROR", str(exc))

        self.root.destroy()

    def _start_services(self) -> None:
        """Không chờ các peer: bật Bully, sau đó duy trì TCP trong nền."""
        if self._stop_event.is_set():
            return
        self.bully.start()
        if self._stop_event.is_set():
            return
        self.update_status()
        self.refresh_balance_async()
        for node_config in self.config.get("nodes", []):
            if self._stop_event.is_set():
                return
            if int(node_config["id"]) != self.node_id:
                threading.Thread(
                    target=self._maintain_peer_connection, args=(dict(node_config),), daemon=True,
                ).start()

    def _maintain_peer_connection(self, node_config: dict) -> None:
        """Mỗi peer một worker để máy offline không làm chậm máy khác."""
        peer_id = int(node_config["id"])
        while not self._stop_event.is_set():
            try:
                if not self.server.is_connected(peer_id):
                    self.server.connect_configured_peer(node_config)
            except Exception as exc:
                self._ui_log(f"[Mạng] Lỗi kết nối tới nút {peer_id}: {exc}")
            if self._stop_event.wait(NETWORK_RETRY_INTERVAL):
                return

    def _periodic_update(self) -> None:
        """Vòng nền cập nhật giao diện và kiểm tra coordinator còn sống."""
        while not self._stop_event.wait(STATUS_UPDATE_INTERVAL):
            try:
                self.update_status()
                self._check_pending_timeout()
            except Exception as exc:
                log_event(self.node_id, "PERIODIC_ERROR", str(exc))

    # ------------------------------------------------------------------
    # Cập nhật trạng thái và số dư
    # ------------------------------------------------------------------

    def _ui_log(self, message: str) -> None:
        """Ghi log vừa ra file/màn hình vừa lên History Log của UI."""
        log_event(self.node_id, "UI", message)
        self.app.post_event("log", message)

    def update_status(self) -> None:
        """Đẩy vai trò, coordinator và kiểm tra đổi coordinator lên UI."""
        if self.bully.is_coordinator():
            role = "Điều phối viên"
        else:
            role = "Nút bình thường"
        self.app.post_event("role", role)

        coordinator_id = self.bully.coordinator_id
        if coordinator_id is None:
            coordinator_text = "chưa xác định"
        else:
            coordinator_text = str(coordinator_id)
        self.app.post_event("coordinator", coordinator_text)

        with self._pending_lock:
            is_waiting = self._pending_type is not None and not self._transaction_running

        # Chỉ coi là "đổi coordinator" khi đã từng biết coordinator cũ.
        # Lần đầu (giá trị None) không tính, tránh huỷ oan đơn vừa gửi.
        coordinator_changed = (
            self._last_coordinator_id is not None
            and self._last_coordinator_id != coordinator_id
        )

        if is_waiting and coordinator_changed:
            reason = (
                f"điều phối viên đổi từ {self._last_coordinator_id} "
                f"sang {coordinator_text}"
            )
            self._cancel_pending(reason)

        self._last_coordinator_id = coordinator_id

    def refresh_balance_async(self) -> None:
        """Đọc số dư trong thread nền rồi cập nhật lên UI."""
        balance_thread = threading.Thread(target=self._refresh_balance, daemon=True)
        balance_thread.start()

    def _refresh_balance(self) -> None:
        # Đọc và đưa kết quả lên UI theo thứ tự, tránh số dư cũ ghi đè số dư mới.
        with self._balance_refresh_lock:
            self._read_balance_for_ui()

    def _read_balance_for_ui(self) -> None:
        try:
            balance = self.bank.get_balance()
            self.app.post_event("balance", f"{balance:,}")
        except Exception as exc:
            self._ui_log(f"[CSDL] Lỗi đọc số dư: {exc}")

    def _broadcast_balance_changed(self) -> None:
        """Báo các nút đọc lại DB sau khi giao dịch đã commit và trả khoá."""
        payload = {"type": "BALANCE_CHANGED", "from": self.node_id}
        for peer_id in self.server.peer_ids():
            try:
                sent = self.server.send(peer_id, payload)
                if not sent and self.server.connect_to_peer(peer_id):
                    sent = self.server.send(peer_id, payload)
                if not sent:
                    self._ui_log(f"[Số dư] Chưa gửi được thông báo tới nút {peer_id}")
            except Exception as exc:
                self._ui_log(f"[Số dư] Lỗi gửi thông báo tới nút {peer_id}: {exc}")

    # ------------------------------------------------------------------
    # Trạng thái đơn xin quyền
    # ------------------------------------------------------------------

    def _cancel_pending(self, reason: str) -> None:
        """Huỷ đơn xin quyền đang chờ và báo người dùng bấm lại."""
        with self._pending_lock:
            if self._pending_type is None or self._transaction_running:
                return
            self._pending_type = None
            self._pending_amount = 0
            self._pending_sent_at = None

        self._ui_log(f"[Khoá] Huỷ giao dịch đang chờ ({reason}), vui lòng bấm lại")
        self.app.post_event("transaction_status", "Giao dịch đã huỷ, vui lòng bấm lại")
        self.app.post_event("transaction_busy", False)

    def _mark_acknowledged(self) -> None:
        """Coordinator đã phản hồi: ngừng đếm thời gian chờ."""
        with self._pending_lock:
            self._pending_sent_at = None

    def _check_pending_timeout(self) -> None:
        """Phát hiện coordinator lỗi qua việc REQUEST không được trả lời.

        Đây là cách phát hiện thứ hai trong docs/FLOWS.md mục 2b.
        """
        with self._pending_lock:
            is_waiting = self._pending_type is not None
            sent_at = self._pending_sent_at

        if not is_waiting or sent_at is None:
            return

        elapsed = time.monotonic() - sent_at
        if elapsed < REQUEST_REPLY_TIMEOUT:
            return

        # Chỉ báo một lần cho đơn này, tránh spam log mỗi vòng cập nhật.
        self._mark_acknowledged()
        self._ui_log("Điều phối viên không phản hồi, bắt đầu bầu chọn")
        self.bully.trigger_election("REQUEST_NO_REPLY")

    # ------------------------------------------------------------------
    # Xin quyền và thực hiện giao dịch
    # ------------------------------------------------------------------

    def request_transaction(self, tx_type: str, amount: int) -> None:
        """Người dùng bấm Nạp/Rút: gửi REQUEST tới coordinator."""
        coordinator_id = self.bully.coordinator_id
        if coordinator_id is None:
            self._ui_log("Chưa xác định điều phối viên, không thể xin quyền")
            return

        with self._pending_lock:
            if self._pending_type is not None:
                self._ui_log("Đang chờ quyền cho giao dịch trước, vui lòng đợi")
                return

            self._pending_type = tx_type
            self._pending_amount = amount
            self._pending_sent_at = time.monotonic()

        # Đơn này gửi tới coordinator hiện tại, nên mốc so sánh "đổi
        # coordinator" phải là người này.
        self._last_coordinator_id = coordinator_id
        action = "nạp" if tx_type == "DEPOSIT" else "rút"
        self.app.post_event("transaction_busy", True)
        self.app.post_event("transaction_status", f"Đang chờ quyền {action} {amount:,} VND…")
        self._ui_log(
            f"Gửi yêu cầu xin quyền tới điều phối viên {coordinator_id} "
            f"({'nạp' if tx_type == 'DEPOSIT' else 'rút'} {amount:,})"
        )

        if self.bully.is_coordinator():
            # Node này là coordinator: tự cấp quyền cho chính mình.
            result = self.bank.request_lock(self.node_id)
            self._on_lock_request(self.node_id, result)
            return

        payload = {"type": "REQUEST", "from": self.node_id}
        if not self.server.send(coordinator_id, payload):
            self._ui_log("Không gửi được yêu cầu xin quyền, bắt đầu bầu chọn")
            self.bully.trigger_election("REQUEST_SEND_FAIL")

    def _on_lock_request(self, requester: int, result: str) -> None:
        """Coordinator đã quyết xong một đơn xin quyền.

        `result` là "GRANTED" hoặc "QUEUED". Node xin quyền cần biết kết quả
        để phân biệt "đang xếp hàng" với "coordinator đã chết".
        """
        result_text = "được cấp quyền" if result == "GRANTED" else "đang xếp hàng"
        self._ui_log(f"[Khoá] Nút {requester} xin quyền: {result_text}")

        if result == "GRANTED":
            self._grant_to(requester)
            return

        if requester == self.node_id:
            self._mark_acknowledged()
            self.app.post_event("transaction_status", "Đang xếp hàng chờ quyền giao dịch…")
        else:
            self.server.send(requester, {"type": "QUEUED", "from": self.node_id})

    def _grant_to(self, requester: int) -> None:
        """Cấp quyền cho một node (có thể là chính mình)."""
        if requester == self.node_id:
            self._handle_grant_lock()
            return

        self.server.send(requester, {"type": "GRANT", "from": self.node_id})

    def _handle_grant_lock(self) -> None:
        """Nhận GRANT: chạy giao dịch đang chờ trong thread nền."""
        self._mark_acknowledged()

        with self._pending_lock:
            if self._transaction_running:
                return
            tx_type = self._pending_type
            amount = self._pending_amount
            if tx_type:
                self._transaction_running = True

        if not tx_type:
            self._ui_log("[Khoá] Nhận quyền nhưng không còn giao dịch chờ, trả quyền ngay")
            # Phải trả quyền ngay. Nếu im lặng, coordinator vẫn ghi nhận node
            # này đang giữ khoá và hàng đợi FIFO bị kẹt vô hạn.
            self.release_lock()
            return

        self._ui_log(
            f"[Khoá] Được cấp quyền, thực hiện "
            f"{'nạp' if tx_type == 'DEPOSIT' else 'rút'} {amount:,}"
        )
        self.app.post_event("transaction_status", "Đã được cấp quyền, đang đọc số dư…")

        transaction_thread = threading.Thread(
            target=self._do_transaction,
            args=(tx_type, amount),
            daemon=True,
        )
        transaction_thread.start()

    def _wait_transaction_delay(self, tx_type: str, amount: int) -> None:
        """Đếm theo đồng hồ thực trong luồng giao dịch, UI chỉ nhận sự kiện."""
        action = "nạp" if tx_type == "DEPOSIT" else "rút"
        deadline = time.monotonic() + self.transaction_delay
        while True:
            if self._stop_event.is_set():
                raise RuntimeError("Nút đã dừng, huỷ giao dịch trước khi ghi")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.app.post_event(
                "transaction_status",
                f"Đang {action} {amount:,} VND — còn {math.ceil(remaining)} giây",
            )
            self._stop_event.wait(min(1.0, remaining))
        self.app.post_event(
            "transaction_status", f"Đang {action} {amount:,} VND — còn 0 giây, đang ghi dữ liệu…",
        )

    def _do_transaction(self, tx_type: str, amount: int) -> None:
        """Thực hiện giao dịch sau khi đã được cấp quyền (chạy ở thread nền)."""
        succeeded = False
        try:
            if tx_type == "DEPOSIT":
                succeeded = self.bank.deposit(
                    self.node_id, amount, before_write=lambda: self._wait_transaction_delay(tx_type, amount),
                )
                if succeeded:
                    message = f"Nạp {amount:,} thành công"
                else:
                    message = f"Nạp {amount:,} thất bại"
            else:
                succeeded = self.bank.withdraw(
                    self.node_id, amount, before_write=lambda: self._wait_transaction_delay(tx_type, amount),
                )
                if succeeded:
                    message = f"Rút {amount:,} thành công"
                else:
                    message = f"Rút {amount:,} thất bại (số dư không đủ hoặc số tiền không hợp lệ)"

            self._ui_log(f"[Giao dịch] {message}")
            self.app.post_event("transaction_status", message)
        except Exception as exc:
            self._ui_log(f"[Giao dịch] Lỗi giao dịch: {exc}")
            self.app.post_event("transaction_status", f"Giao dịch thất bại: {exc}")
        finally:
            # Xoá đơn TRƯỚC khi trả khoá. Nếu trả khoá trước, coordinator có
            # thể cấp quyền lại cho chính node này và giao dịch chạy lặp.
            with self._pending_lock:
                self._pending_type = None
                self._pending_amount = 0
                self._pending_sent_at = None
                self._transaction_running = False

            try:
                self.release_lock()
            finally:
                self.app.post_event("transaction_busy", False)
                self.refresh_balance_async()

        if succeeded:
            self._broadcast_balance_changed()

    def release_lock(self) -> None:
        """Trả quyền truy cập số dư về cho coordinator."""
        if self.bully.is_coordinator():
            # Server không lưu kết nối tới chính mình, nên giải phóng trực tiếp.
            next_holder = self.bank.release_lock(self.node_id)
            self._deliver_next(self.node_id, next_holder)
            return

        coordinator_id = self.bully.coordinator_id
        if coordinator_id is None:
            return

        payload = {"type": "RELEASE", "from": self.node_id}
        if not self.server.send(coordinator_id, payload):
            self._ui_log("Không trả được quyền, điều phối viên có thể đã tắt")

    def _deliver_next(self, holder: int, next_holder) -> None:
        """Coordinator cấp quyền cho node kế tiếp trong hàng đợi FIFO."""
        self._ui_log(
            f"[Khoá] Nút {holder} trả quyền; "
            f"{'hàng đợi trống' if next_holder is None else f'cấp tiếp cho nút {next_holder}'}"
        )

        if next_holder is None:
            return

        self._grant_to(int(next_holder))

    # ------------------------------------------------------------------
    # Nhận gói tin từ mạng
    # ------------------------------------------------------------------

    def on_message(self, node_id: int, message: dict, peer_id, conn) -> None:
        """Xử lý một gói tin vừa nhận từ NetworkServer."""
        message_type = message.get("type")
        if message_type == "BALANCE_CHANGED":
            sender = message.get("from")
            if sender != self.node_id and sender in self.server.peer_ids():
                self._ui_log(f"[Số dư] Nút {sender} vừa giao dịch, đang cập nhật số dư")
                self.refresh_balance_async()
            return

        was_coordinator = self.bully.is_coordinator()

        if message_type in BULLY_MESSAGE_TYPES or message_type in ("JOIN", "JOIN_ACK"):
            if message_type in ("JOIN", "JOIN_ACK"):
                member = message.get("node", {})
                self._ui_log(
                    f"[Mạng] Đã kết nối với nút {peer_id} "
                    f"({member.get('ip')}:{member.get('port')})"
                )
                self.bully.on_peer_joined(int(peer_id))
            else:
                self.bully.handle_message(node_id, message, peer_id, conn)

            if was_coordinator and not self.bully.is_coordinator():
                # Vừa mất vai trò coordinator: hàng đợi khoá cũ không còn
                # ai trả lời được nữa (docs/FLOWS.md mục 3).
                self.bank.reset_lock()
                self._ui_log("[Khoá] Xoá trạng thái khoá vì không còn là điều phối viên")

            self.update_status()
            return

        if message_type == "REQUEST" and self.bully.is_coordinator():
            requester = message.get("from")
            if requester is not None:
                result = self.bank.request_lock(int(requester))
                self._on_lock_request(int(requester), result)
            return

        if message_type == "RELEASE" and self.bully.is_coordinator():
            holder = message.get("from")
            if holder is not None:
                next_holder = self.bank.release_lock(int(holder))
                self._deliver_next(int(holder), next_holder)
            return

        if message_type == "GRANT":
            self._handle_grant_lock()
            return

        if message_type == "QUEUED":
            self._mark_acknowledged()
            with self._pending_lock:
                is_waiting = self._pending_type is not None and not self._transaction_running
            if is_waiting:
                self.app.post_event("transaction_status", "Đang xếp hàng chờ quyền giao dịch…")
            sender = message.get("from")
            self._ui_log(
                f"[Khoá] Điều phối viên {sender} đã nhận đơn, đang xếp hàng chờ quyền"
            )
            return


def main() -> None:
    """Khởi động một node từ tham số dòng lệnh --id."""
    parser = argparse.ArgumentParser(
        description="Chạy một nút trong Hệ thống Quỹ chung Phân tán"
    )
    parser.add_argument("--id", type=int, required=True, help="ID của nút (0-5)")
    args = parser.parse_args()
    node_id = args.id

    config = load_config()
    node_config = find_node_config(config, node_id)
    if node_config is None:
        print(f"Không tìm thấy nút id={node_id} trong config.json", file=sys.stderr)
        sys.exit(1)

    host = node_config.get("ip", "0.0.0.0")
    port = int(node_config.get("port", 3000 + node_id))
    log_event(node_id, "NODE_START", f"Nút {node_id}, địa chỉ {host}:{port}")

    # Mạng phải sẵn sàng trước UI. Nếu bind thất bại thì dừng ngay, không mở
    # giao diện chạy mà không ai kết nối được tới.
    server = start_node(node_id)
    if server is None:
        print(
            f"Nút {node_id} không mở được cổng tại {host}:{port}. "
            "Kiểm tra cổng đang bị chiếm hoặc IP trong config.json "
            "không phải IP của máy này.",
            file=sys.stderr,
        )
        sys.exit(1)

    root = tk.Tk()
    app = AppWindow(root, node_id)
    app.post_event("log", f"Nút {node_id} khởi động tại {host}:{port}")

    bank = BankService()
    bully = Bully(server, node_id)

    runtime = NodeRuntime(
        root=root,
        node_id=node_id,
        config=config,
        server=server,
        app=app,
        bank=bank,
        bully=bully,
    )
    runtime.start()

    root.protocol("WM_DELETE_WINDOW", runtime.shutdown)
    root.mainloop()


if __name__ == "__main__":
    main()

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
    4. Chạy các dịch vụ nền: Bully, kết nối tới các peer, vòng cập nhật.
    5. Chờ người dùng bấm Nạp/Rút.

Cách chạy:
    python main.py --id 0
"""

import argparse
import json
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

# Thời gian chờ coordinator phản hồi sau khi gửi REQUEST_LOCK.
# Quá hạn này thì coi coordinator đã lỗi (docs/FLOWS.md mục 2b).
LOCK_REPLY_TIMEOUT = 10.0

# Chu kỳ của vòng cập nhật trạng thái nền.
STATUS_UPDATE_INTERVAL = 2.0


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
        _pending_sent_at : thời điểm gửi REQUEST_LOCK (time.monotonic()).
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
    ):
        self.root = root
        self.node_id = node_id
        self.config = config
        self.server = server
        self.app = app
        self.bank = bank
        self.bully = bully

        # Bảo vệ ba biến _pending_* vì chúng bị nhiều thread cùng đọc/ghi.
        self._pending_lock = threading.Lock()
        self._pending_type = None
        self._pending_amount = 0
        self._pending_sent_at = None

        # Coordinator gần nhất mà node này biết, dùng để phát hiện đổi
        # coordinator giữa chừng.
        self._last_coordinator_id = None

    # ------------------------------------------------------------------
    # Khởi động và tắt
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Gắn callback, bật Bully và chạy các luồng nền."""
        self.app.on_deposit = lambda amount: self.request_transaction("DEPOSIT", amount)
        self.app.on_withdraw = lambda amount: self.request_transaction("WITHDRAW", amount)

        self.server.set_message_handler(self.on_message)
        self.bully.start()

        connect_thread = threading.Thread(target=self._connect_peers, daemon=True)
        connect_thread.start()

        periodic_thread = threading.Thread(target=self._periodic_update, daemon=True)
        periodic_thread.start()

    def shutdown(self) -> None:
        """Dừng mọi dịch vụ và đóng cửa sổ."""
        self._ui_log("Dang dung node...")

        try:
            self.bully.stop()
            self.server.stop()
            self.bank.close()
        except Exception as exc:
            log_event(self.node_id, "STOP_ERROR", str(exc))

        self.root.destroy()

    def _connect_peers(self) -> None:
        """Chờ server sẵn sàng rồi nối tới tất cả các peer trong config."""
        time.sleep(0.5)

        for node_config in self.config.get("nodes", []):
            peer_id = int(node_config.get("id"))
            if peer_id != self.node_id:
                self.server.connect_to_peer(peer_id)

        time.sleep(0.5)
        self.update_status()
        self.refresh_balance_async()

    def _periodic_update(self) -> None:
        """Vòng nền cập nhật giao diện và kiểm tra coordinator còn sống."""
        while True:
            time.sleep(STATUS_UPDATE_INTERVAL)
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
            role = "Coordinator"
        else:
            role = "Node binh thuong"
        self.app.post_event("role", role)

        coordinator_id = self.bully.coordinator_id
        if coordinator_id is None:
            coordinator_text = "chua xac dinh"
        else:
            coordinator_text = str(coordinator_id)
        self.app.post_event("coordinator", coordinator_text)

        with self._pending_lock:
            is_waiting = self._pending_type is not None

        # Chỉ coi là "đổi coordinator" khi đã từng biết coordinator cũ.
        # Lần đầu (giá trị None) không tính, tránh huỷ oan đơn vừa gửi.
        coordinator_changed = (
            self._last_coordinator_id is not None
            and self._last_coordinator_id != coordinator_id
        )

        if is_waiting and coordinator_changed:
            reason = (
                f"coordinator doi tu {self._last_coordinator_id} "
                f"sang {coordinator_text}"
            )
            self._cancel_pending(reason)

        self._last_coordinator_id = coordinator_id

    def refresh_balance_async(self) -> None:
        """Đọc số dư trong thread nền rồi cập nhật lên UI."""
        balance_thread = threading.Thread(target=self._refresh_balance, daemon=True)
        balance_thread.start()

    def _refresh_balance(self) -> None:
        try:
            balance = self.bank.get_balance()
            self.app.post_event("balance", f"{balance:,}")
        except Exception as exc:
            self.app.post_event("log", f"[DB] Loi doc so du: {exc}")

    # ------------------------------------------------------------------
    # Trạng thái đơn xin quyền
    # ------------------------------------------------------------------

    def _cancel_pending(self, reason: str) -> None:
        """Huỷ đơn xin quyền đang chờ và báo người dùng bấm lại."""
        with self._pending_lock:
            if self._pending_type is None:
                return
            self._pending_type = None
            self._pending_amount = 0
            self._pending_sent_at = None

        self._ui_log(f"[LOCK] Huy giao dich dang cho ({reason}), vui long bam lai")

    def _mark_acknowledged(self) -> None:
        """Coordinator đã phản hồi: ngừng đếm thời gian chờ."""
        with self._pending_lock:
            self._pending_sent_at = None

    def _check_pending_timeout(self) -> None:
        """Phát hiện coordinator lỗi qua việc REQUEST_LOCK không được trả lời.

        Đây là cách phát hiện thứ hai trong docs/FLOWS.md mục 2b.
        """
        with self._pending_lock:
            is_waiting = self._pending_type is not None
            sent_at = self._pending_sent_at

        if not is_waiting or sent_at is None:
            return

        elapsed = time.monotonic() - sent_at
        if elapsed < LOCK_REPLY_TIMEOUT:
            return

        # Chỉ báo một lần cho đơn này, tránh spam log mỗi vòng cập nhật.
        self._mark_acknowledged()
        self._ui_log("Dieu phoi vien khong phan hoi, bat dau bau chon")
        self.bully.trigger_election("REQUEST_LOCK_NO_REPLY")

    # ------------------------------------------------------------------
    # Xin quyền và thực hiện giao dịch
    # ------------------------------------------------------------------

    def request_transaction(self, tx_type: str, amount: int) -> None:
        """Người dùng bấm Nạp/Rút: gửi REQUEST_LOCK tới coordinator."""
        coordinator_id = self.bully.coordinator_id
        if coordinator_id is None:
            self._ui_log("Chua xac dinh coordinator, khong the xin quyen")
            return

        with self._pending_lock:
            if self._pending_type is not None:
                self._ui_log("Dang cho quyen cho giao dich truoc, vui long doi")
                return

            self._pending_type = tx_type
            self._pending_amount = amount
            self._pending_sent_at = time.monotonic()

        # Đơn này gửi tới coordinator hiện tại, nên mốc so sánh "đổi
        # coordinator" phải là người này.
        self._last_coordinator_id = coordinator_id
        self._ui_log(
            f"Gui REQUEST_LOCK toi coordinator {coordinator_id} "
            f"({tx_type} {amount:,})"
        )

        if self.bully.is_coordinator():
            # Node này là coordinator: tự cấp quyền cho chính mình.
            result = self.bank.request_lock(self.node_id)
            self._on_lock_request(self.node_id, result)
            return

        payload = {"type": "REQUEST_LOCK", "from": self.node_id}
        if not self.server.send(coordinator_id, payload):
            self._ui_log("Khong gui duoc REQUEST_LOCK, bat dau bau chon")
            self.bully.trigger_election("REQUEST_LOCK_SEND_FAIL")

    def _on_lock_request(self, requester: int, result: str) -> None:
        """Coordinator đã quyết xong một đơn xin quyền.

        `result` là "GRANTED" hoặc "QUEUED". Node xin quyền cần biết kết quả
        để phân biệt "đang xếp hàng" với "coordinator đã chết".
        """
        self.app.post_event("log", f"[LOCK] REQUEST_LOCK tu node {requester} -> {result}")

        if result == "GRANTED":
            self._grant_to(requester)
            return

        if requester == self.node_id:
            self._mark_acknowledged()
        else:
            self.server.send(requester, {"type": "LOCK_QUEUED", "from": self.node_id})

    def _grant_to(self, requester: int) -> None:
        """Cấp quyền cho một node (có thể là chính mình)."""
        if requester == self.node_id:
            self._handle_grant_lock()
            return

        self.server.send(requester, {"type": "GRANT_LOCK", "from": self.node_id})

    def _handle_grant_lock(self) -> None:
        """Nhận GRANT_LOCK: chạy giao dịch đang chờ trong thread nền."""
        self._mark_acknowledged()

        with self._pending_lock:
            tx_type = self._pending_type
            amount = self._pending_amount

        if not tx_type:
            self.app.post_event(
                "log",
                "[LOCK] Nhan GRANT_LOCK nhung khong co giao dich cho",
            )
            # Phải trả quyền ngay. Nếu im lặng, coordinator vẫn ghi nhận node
            # này đang giữ khoá và hàng đợi FIFO bị kẹt vô hạn.
            self.release_lock()
            return

        self.app.post_event(
            "log",
            f"[LOCK] Duoc cap quyen, thuc hien {tx_type} {amount:,}",
        )

        transaction_thread = threading.Thread(
            target=self._do_transaction,
            args=(tx_type, amount),
            daemon=True,
        )
        transaction_thread.start()

    def _do_transaction(self, tx_type: str, amount: int) -> None:
        """Thực hiện giao dịch sau khi đã được cấp quyền (chạy ở thread nền)."""
        try:
            if tx_type == "DEPOSIT":
                succeeded = self.bank.deposit(self.node_id, amount)
                if succeeded:
                    message = f"Nap {amount:,} thanh cong"
                else:
                    message = f"Nap {amount:,} that bai"
            else:
                succeeded = self.bank.withdraw(self.node_id, amount)
                if succeeded:
                    message = f"Rut {amount:,} thanh cong"
                else:
                    message = f"Rut {amount:,} that bai (so du khong du?)"

            self.app.post_event("log", f"[TX] {message}")
        except Exception as exc:
            self.app.post_event("log", f"[TX] Loi giao dich: {exc}")
        finally:
            # Xoá đơn TRƯỚC khi trả khoá. Nếu trả khoá trước, coordinator có
            # thể cấp quyền lại cho chính node này và giao dịch chạy lặp.
            with self._pending_lock:
                self._pending_type = None
                self._pending_amount = 0
                self._pending_sent_at = None

            self.release_lock()
            self.refresh_balance_async()

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

        payload = {"type": "RELEASE_LOCK", "from": self.node_id}
        if not self.server.send(coordinator_id, payload):
            self._ui_log("Khong gui duoc RELEASE_LOCK, coordinator co the da tat")

    def _deliver_next(self, holder: int, next_holder) -> None:
        """Coordinator cấp quyền cho node kế tiếp trong hàng đợi FIFO."""
        self.app.post_event(
            "log",
            f"[LOCK] RELEASE_LOCK tu node {holder}, next={next_holder}",
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
        was_coordinator = self.bully.is_coordinator()

        if message_type in BULLY_MESSAGE_TYPES:
            self.bully.handle_message(node_id, message, peer_id, conn)

            if was_coordinator and not self.bully.is_coordinator():
                # Vừa mất vai trò coordinator: hàng đợi khoá cũ không còn
                # ai trả lời được nữa (docs/FLOWS.md mục 3).
                self.bank.reset_lock()
                self._ui_log("[LOCK] Xoa trang thai khoa vi khong con la coordinator")

            self.update_status()
            return

        if message_type == "REQUEST_LOCK" and self.bully.is_coordinator():
            requester = message.get("from")
            if requester is not None:
                result = self.bank.request_lock(int(requester))
                self._on_lock_request(int(requester), result)
            return

        if message_type == "RELEASE_LOCK" and self.bully.is_coordinator():
            holder = message.get("from")
            if holder is not None:
                next_holder = self.bank.release_lock(int(holder))
                self._deliver_next(int(holder), next_holder)
            return

        if message_type == "GRANT_LOCK":
            self._handle_grant_lock()
            return

        if message_type == "LOCK_QUEUED":
            self._mark_acknowledged()
            sender = message.get("from")
            self._ui_log(
                f"[LOCK] Coordinator {sender} da nhan don, dang xep hang cho quyen"
            )
            return


def main() -> None:
    """Khởi động một node từ tham số dòng lệnh --id."""
    parser = argparse.ArgumentParser(
        description="Chay mot node trong He thong Quy chung Phan tan"
    )
    parser.add_argument("--id", type=int, required=True, help="ID cua node (0-5)")
    args = parser.parse_args()
    node_id = args.id

    config = load_config()
    node_config = find_node_config(config, node_id)
    if node_config is None:
        print(f"Khong tim thay node id={node_id} trong config.json", file=sys.stderr)
        sys.exit(1)

    host = node_config.get("ip", "0.0.0.0")
    port = int(node_config.get("port", 3000 + node_id))
    log_event(node_id, "NODE_START", f"id={node_id} host={host} port={port}")

    # Mạng phải sẵn sàng trước UI. Nếu bind thất bại thì dừng ngay, không mở
    # giao diện chạy mà không ai kết nối được tới.
    server = start_node(node_id)
    if server is None:
        print(
            f"Node {node_id} khong bind duoc {host}:{port}. "
            "Kiem tra port dang bi chiem hoac IP trong config.json "
            "khong phai IP cua may nay.",
            file=sys.stderr,
        )
        sys.exit(1)

    root = tk.Tk()
    app = AppWindow(root, node_id)
    app.post_event("log", f"Node {node_id} khoi dong tai {host}:{port}")

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

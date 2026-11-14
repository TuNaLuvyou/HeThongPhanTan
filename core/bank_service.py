# core/bank_service.py - Thành viên 4 (Linux 2)
"""Quỹ chung: đọc/ghi số dư trong DB và giữ khoá loại trừ tương hỗ.

Hai phần việc nằm trong file này:

1. Khoá loại trừ tương hỗ (chạy tại coordinator):
   - request_lock() : node xin quyền truy cập số dư.
   - release_lock() : node trả quyền, coordinator cấp cho node kế tiếp.
   - reset_lock()   : xoá sạch khoá khi node không còn là coordinator.

2. Truy cập số dư (chỉ node đang giữ khoá mới được gọi):
   - get_balance() / deposit() / withdraw()

Số dư nằm trong bảng `fund` của Postgres (Supabase), không nằm trong bộ nhớ.
Giao dịch được ghi lại vào bảng `transactions`.

Tài liệu tham chiếu: docs/TECHS.md (mục 7, 8), docs/FLOWS.md (mục 4, 5).
"""

import os
import threading
from pathlib import Path
from typing import Callable, Optional

import psycopg2

from core.network import log_event

# Số dư ban đầu, dùng khi bảng fund chưa có dòng nào.
INITIAL_BALANCE = 10000000

# Kết quả trả về của request_lock().
GRANTED = "GRANTED"
QUEUED = "QUEUED"


def _database_url_from_env_file() -> Optional[str]:
    """Đọc DATABASE_URL từ .env ở gốc dự án, không phụ thuộc thư mục chạy."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return None

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() == "DATABASE_URL":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
    return None


class BankService:
    """Quản lý số dư trong DB và hàng đợi cấp quyền của coordinator."""

    def __init__(self, database_url: Optional[str] = None):
        if database_url is not None:
            self.database_url = database_url
        else:
            self.database_url = os.getenv("DATABASE_URL") or _database_url_from_env_file()

        self._conn = None
        self._lock_holder: Optional[int] = None
        self._lock_queue: list = []
        # Chỉ giữ mutex khi đổi trạng thái cấp quyền, không giữ lúc giao dịch.
        self._access_mutex = threading.Lock()

    # ------------------------------------------------------------------
    # Kết nối DB
    # ------------------------------------------------------------------

    def _connect(self):
        """Mở kết nối tới DB nếu chưa có, rồi trả về kết nối đó."""
        if self._conn is not None:
            return self._conn

        if not self.database_url:
            raise RuntimeError(
                "Thiếu DATABASE_URL. Vui lòng điền DATABASE_URL trong .env"
            )

        self._conn = psycopg2.connect(self.database_url)
        return self._conn

    def close(self) -> None:
        """Đóng kết nối DB nếu đang mở."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Khoá loại trừ tương hỗ
    # ------------------------------------------------------------------

    def request_lock(self, node_id: int) -> str:
        """Node xin quyền truy cập số dư.

        Trả về:
        - GRANTED: khoá đang rảnh (hoặc chính node này đang giữ), cấp ngay.
        - QUEUED : node khác đang giữ khoá, node này được xếp vào hàng đợi.
        """
        with self._access_mutex:
            lock_is_free = self._lock_holder is None
            already_holds_lock = self._lock_holder == node_id
            if lock_is_free or already_holds_lock:
                self._lock_holder = node_id
                result = GRANTED
            else:
                self._lock_queue.append(node_id)
                result = QUEUED
            holder = self._lock_holder
            queue_snapshot = list(self._lock_queue)

        if result == GRANTED:
            log_event(node_id, "GRANTED", f"Nút giữ quyền: {holder}")
            return result
        log_event(
            node_id,
            "QUEUED",
            f"Nút giữ quyền: {holder}; hàng đợi: {queue_snapshot}",
        )
        return QUEUED

    def release_lock(self, holder_id: int) -> Optional[int]:
        """Node đang giữ khoá trả quyền lại cho coordinator.

        Trả về ID của node kế tiếp trong hàng đợi FIFO, hoặc None nếu hàng đợi
        rỗng. Nếu người gọi không phải node đang giữ khoá thì bỏ qua.
        """
        with self._access_mutex:
            if self._lock_holder != holder_id:
                return None
            # Trả quyền và chuyển sang người kế tiếp trong cùng một lần giữ mutex.
            next_holder = self._lock_queue.pop(0) if self._lock_queue else None
            self._lock_holder = next_holder
        if next_holder is None:
            return None
        log_event(next_holder, "GRANTED", f"Nút giữ quyền: {next_holder}")
        return next_holder

    def reset_lock(self) -> None:
        """Xoá toàn bộ trạng thái khoá khi node không còn là coordinator.

        Hàng đợi cũ thuộc về coordinator cũ. Nếu giữ lại, coordinator mới sẽ
        cấp quyền cho những node không còn chờ được nữa (FLOWS mục 3).
        """
        with self._access_mutex:
            lock_is_empty = self._lock_holder is None and not self._lock_queue
            if lock_is_empty:
                return
            old_holder = self._lock_holder
            old_queue = list(self._lock_queue)
            self._lock_holder = None
            self._lock_queue = []

        log_event(
            -1,
            "ACCESS_RESET",
            f"Nút giữ quyền cũ: {old_holder}; hàng đợi cũ: {old_queue}",
        )

    # ------------------------------------------------------------------
    # Đọc và ghi số dư
    # ------------------------------------------------------------------

    def get_balance(self) -> int:
        """Đọc số dư bằng kết nối riêng, không dùng chung phiên đang ghi."""
        if not self.database_url:
            raise RuntimeError("Thiếu DATABASE_URL. Vui lòng điền DATABASE_URL trong .env")
        conn = psycopg2.connect(self.database_url)
        try:
            with conn:
                with conn.cursor() as cursor:
                    return self._read_balance(cursor, INITIAL_BALANCE)
        finally:
            conn.close()

    def deposit(self, node_id: int, amount: int, before_write: Optional[Callable[[], None]] = None) -> bool:
        """Nạp tiền vào quỹ: balance += amount, ghi một dòng DEPOSIT."""
        if not self._is_valid_amount(amount):
            log_event(node_id, "DEPOSIT_INVALID", f"Số tiền không hợp lệ: {amount}")
            return False

        conn = self._connect()
        with conn.cursor() as cursor:
            # Cố ý đọc rồi tính rồi ghi, KHÔNG dùng "balance = balance + x".
            # Cách này để lộ mất cập nhật nếu khoá loại trừ bị tắt, phục vụ demo.
            balance = self._read_balance(cursor, INITIAL_BALANCE)
            new_balance = balance + amount
            if before_write is not None:
                try:
                    before_write()
                except Exception:
                    conn.rollback()
                    raise

            cursor.execute(
                "UPDATE fund SET balance = %s, updated_at = now() WHERE id = 1",
                (new_balance,),
            )
            cursor.execute(
                "INSERT INTO transactions (node_id, type, amount, created_at) "
                "VALUES (%s, 'DEPOSIT', %s, now())",
                (node_id, amount),
            )

        conn.commit()
        log_event(node_id, "DEPOSIT_OK", f"Nạp {amount:,}; số dư mới: {new_balance:,}")
        return True

    def withdraw(self, node_id: int, amount: int, before_write: Optional[Callable[[], None]] = None) -> bool:
        """Rút tiền khỏi quỹ nếu đủ số dư, ghi một dòng WITHDRAW.

        Trả về False nếu số tiền không hợp lệ hoặc số dư không đủ.
        """
        if not self._is_valid_amount(amount):
            log_event(node_id, "WITHDRAW_INVALID", f"Số tiền không hợp lệ: {amount}")
            return False

        conn = self._connect()
        with conn.cursor() as cursor:
            balance = self._read_balance(cursor, 0)

            if balance < amount:
                log_event(
                    node_id,
                    "WITHDRAW_REJECT",
                    f"Yêu cầu rút: {amount:,}; số dư hiện tại: {balance:,}",
                )
                return False

            new_balance = balance - amount
            if before_write is not None:
                try:
                    before_write()
                except Exception:
                    conn.rollback()
                    raise

            cursor.execute(
                "UPDATE fund SET balance = %s, updated_at = now() WHERE id = 1",
                (new_balance,),
            )
            cursor.execute(
                "INSERT INTO transactions (node_id, type, amount, created_at) "
                "VALUES (%s, 'WITHDRAW', %s, now())",
                (node_id, amount),
            )

        conn.commit()
        log_event(node_id, "WITHDRAW_OK", f"Rút {amount:,}; số dư mới: {new_balance:,}")
        return True

    # ------------------------------------------------------------------
    # Hàm phụ
    # ------------------------------------------------------------------

    @staticmethod
    def _read_balance(cursor, default_balance: int) -> int:
        """Đọc số dư từ bảng fund, trả về `default_balance` nếu chưa có dòng."""
        cursor.execute("SELECT balance FROM fund WHERE id = 1")
        row = cursor.fetchone()

        if row is None:
            return default_balance

        return int(row[0])

    @staticmethod
    def _is_valid_amount(amount: int) -> bool:
        """Số tiền phải là số nguyên dương."""
        return isinstance(amount, int) and amount > 0

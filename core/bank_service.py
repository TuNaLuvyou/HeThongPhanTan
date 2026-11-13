# core/bank_service.py - Thanh vien 4 (Linux 2) - Quy chung: truy cap DB Supabase va khoa loai tru ho tro (REQUEST_LOCK, GRANT_LOCK, RELEASE_LOCK)
# Doc tham chieu: docs/TECHS.md (muc 7, 8), docs/FLOWS.md (muc 4, 5), docs/BUSINESS.md (muc 3, 5)

import os
from typing import Optional

import psycopg2

from core.network import log_event

INITIAL_BALANCE = 10000000  # So du ban dau (config.json: shared_balance_initial)
LOCK_GRANTED = "GRANTED"
LOCK_QUEUED = "QUEUED"


class BankService:
    """Quan ly quy chung: so du trong DB + khoa loai tru ho tro giao dich nha.

    - So du nam trong CSDL (Supabase Postgres), khong nam trong memory.
    - Toi da giu lock (REQUEST_LOCK / GRANT_LOCK / RELEASE_LOCK).
    - Giao dich: doc so du -> tinh so mu -> ghi (KHONG dung `balance = balance + x`).
    """

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self._conn = None
        self._lock_holder: Optional[int] = None
        self._lock_queue: list = []

    def _connect(self):
        if self._conn is None:
            if not self.database_url:
                raise RuntimeError("Thieu DATABASE_URL. Nguoi dat cau hinh can dien DATABASE_URL tu .env")
            self._conn = psycopg2.connect(self.database_url)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------- Khoa

    def request_lock(self, node_id: int) -> str:
        """Node (thay the coordinator) goi de xin tru cap lock cho node nghiep vu.

        Tra ve:
        - LOCK_GRANTED: coordinator da cap cho (node_id) duoc tru cap.
        - LOCK_QUEUED: he thong dang giu lock o node khac, danh dau node cho vao hang dao.
        """
        if self._lock_holder is None or self._lock_holder == node_id:
            self._lock_holder = node_id
            log_event(node_id, "LOCK_GRANTED", f"holder={node_id}")
            return LOCK_GRANTED
        self._lock_queue.append(node_id)
        log_event(node_id, "LOCK_QUEUED", f"holder={self._lock_holder} queue={self._lock_queue}")
        return LOCK_QUEUED

    def release_lock(self, holder_id: int) -> Optional[int]:
        """Holder (node dang giu lock) goi khi ket thuc nghiep vu.

        Tra ve node duoc cap lock tiep theo (FIFO) neu co, else None.
        """
        if self._lock_holder != holder_id:
            return None
        self._lock_holder = None
        next_holder = None
        if self._lock_queue:
            next_holder = self._lock_queue.pop(0)
            self._lock_holder = next_holder
            log_event(next_holder, "LOCK_GRANTED", f"holder={next_holder}")
        return next_holder

    # --------------------------------------------------------- So du + GD

    def get_balance(self) -> int:
        """Doc so du hien tai tu bang fund."""
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM fund WHERE id = 1")
            row = cur.fetchone()
            balance = int(row[0]) if row is not None else INITIAL_BALANCE
        return balance

    def deposit(self, node_id: int, amount: int) -> bool:
        """Nap tien: balance += amount, ghi transaction DEPOSIT."""
        if not self._is_valid_amount(amount):
            log_event(node_id, "DEPOSIT_INVALID", f"amount={amount}")
            return False
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM fund WHERE id = 1")
            row = cur.fetchone()
            balance = int(row[0]) if row is not None else INITIAL_BALANCE
            new_balance = balance + amount
            cur.execute("UPDATE fund SET balance = %s, updated_at = now() WHERE id = 1", (new_balance,))
            cur.execute(
                "INSERT INTO transactions (node_id, type, amount, created_at) VALUES (%s, 'DEPOSIT', %s, now())",
                (node_id, amount),
            )
        conn.commit()
        log_event(node_id, "DEPOSIT_OK", f"amount={amount} new_balance={new_balance}")
        return True

    def withdraw(self, node_id: int, amount: int) -> bool:
        """Rut tien: kiem tra so du, neu du -> balance -= amount, ghi transaction WITHDRAW.

        Tra ve False neu so du khong du (client se release lock va hien thi loi).
        """
        if not self._is_valid_amount(amount):
            log_event(node_id, "WITHDRAW_INVALID", f"amount={amount}")
            return False
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM fund WHERE id = 1")
            row = cur.fetchone()
            balance = int(row[0]) if row is not None else 0
            if balance < amount:
                log_event(node_id, "WITHDRAW_REJECT", f"amount={amount} balance={balance}")
                return False
            new_balance = balance - amount
            cur.execute("UPDATE fund SET balance = %s, updated_at = now() WHERE id = 1", (new_balance,))
            cur.execute(
                "INSERT INTO transactions (node_id, type, amount, created_at) VALUES (%s, 'WITHDRAW', %s, now())",
                (node_id, amount),
            )
        conn.commit()
        log_event(node_id, "WITHDRAW_OK", f"amount={amount} new_balance={new_balance}")
        return True

    @staticmethod
    def _is_valid_amount(amount: int) -> bool:
        """So tien phai la so nguyen duong."""
        return isinstance(amount, int) and amount > 0

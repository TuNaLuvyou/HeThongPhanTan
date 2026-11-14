# main.py - Truong nhom (macOS ARM) - Tich hop UI + Network + Bully + Bank (demo end-to-end)

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

# Cac loai goi tin do Bully xu ly (xem docs/TECHS.md muc 6).
BULLY_MESSAGES = ("ELECTION", "ANSWER", "COORDINATOR", "HEARTBEAT", "HEARTBEAT_ACK")

# Thoi gian cho mot phan hoi cua coordinator (GRANT_LOCK hoac LOCK_QUEUED) sau khi gui REQUEST_LOCK.
# Qua ngan nay thi coi coordinator da loi theo docs/FLOWS.md muc 2b. Thuoc muc 9b CẦN CHỐT.
LOCK_REPLY_TIMEOUT = 10.0


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Chay mot node trong He thong Quy chung Phan tan")
    parser.add_argument("--id", type=int, required=True, help="ID cua node (0-5)")
    args = parser.parse_args()
    node_id = args.id

    config = load_config()
    node_cfg = None
    for n in config.get("nodes", []):
        if n.get("id") == node_id:
            node_cfg = n
            break
    if node_cfg is None:
        print(f"Khong tim thay node id={node_id} trong config.json", file=sys.stderr)
        sys.exit(1)

    host = node_cfg.get("ip", "0.0.0.0")
    port = int(node_cfg.get("port", 3000 + node_id))
    log_event(node_id, "NODE_START", f"id={node_id} host={host} port={port}")

    # Mang phai san sang truoc UI: neu bind that bai thi dung lai ngay,
    # khong de UI mo len roi chay ma khong co node noi duoc vao.
    server = start_node(node_id)
    if server is None:
        print(
            f"Node {node_id} khong bind duoc {host}:{port}. "
            "Kiem tra port dang bi chiem hoac IP trong config.json khong phai IP cua may nay.",
            file=sys.stderr,
        )
        sys.exit(1)

    # UI (luong chinh)
    root = tk.Tk()
    app = AppWindow(root, node_id)
    app.post_event("log", f"Node {node_id} khoi dong tai {host}:{port}")

    # Dich vu nen
    bank = BankService()
    bully = Bully(server, node_id)

    # Trang thai giao dich dang cho (phia client).
    # sent_at = thoi diem gui REQUEST_LOCK gan nhat, None nghia la coordinator da phan hoi.
    pending_lock = threading.Lock()
    pending_op = {"type": None, "amount": 0, "sent_at": None}  # type: "DEPOSIT" | "WITHDRAW" | None
    last_coordinator = {"id": None}

    def ui_log(msg: str):
        log_event(node_id, "UI", msg)
        app.post_event("log", msg)

    def update_status():
        role = "Coordinator" if bully.is_coordinator() else "Node binh thuong"
        coord = bully.coordinator_id if bully.coordinator_id is not None else "chua xac dinh"
        app.post_event("role", role)
        app.post_event("coordinator", str(coord))

        with pending_lock:
            waiting = pending_op["type"] is not None
        # Chi huy khi da tung biet coordinator cu. Gia tri dau la None (chua tim thay coordinator)
        # nen lan gan nhat dau tien khong duoc tinh la "doi coordinator".
        if waiting and last_coordinator["id"] is not None and last_coordinator["id"] != bully.coordinator_id:
            # Doi coordinator thi don xin quyen cu khong con ai xu ly (TECHS 9d chua chot)
            # -> huy de user bam lai duoc, tranh ket tran lang o nut Nap/Rut.
            cancel_pending(f"coordinator doi tu {last_coordinator['id']} sang {coord}")
        last_coordinator["id"] = bully.coordinator_id

    def refresh_balance_async():
        def _job():
            try:
                bal = bank.get_balance()
                app.post_event("balance", f"{bal:,}")
            except Exception as e:
                app.post_event("log", f"[DB] Loi doc so du: {e}")

        threading.Thread(target=_job, daemon=True).start()

    def cancel_pending(reason: str):
        """Xoa don xin quyen dang cho va bao cho user biet."""
        with pending_lock:
            if pending_op["type"] is None:
                return
            pending_op["type"] = None
            pending_op["amount"] = 0
            pending_op["sent_at"] = None
        ui_log(f"[LOCK] Huy giao dich dang cho ({reason}), vui long bam lai")

    def mark_acknowledged():
        """Coordinator da phan hoi (cap quyen hoac xep hang) -> ngung doi het thoi gian."""
        with pending_lock:
            pending_op["sent_at"] = None

    def do_transaction(tx_type: str, amount: int):
        """Thuc hien giao dich sau khi duoc GRANT_LOCK (chay o thread nen)."""
        try:
            if tx_type == "DEPOSIT":
                ok = bank.deposit(node_id, amount)
                msg = f"Nap {amount:,} thanh cong" if ok else f"Nap {amount:,} that bai"
            else:
                ok = bank.withdraw(node_id, amount)
                msg = f"Rut {amount:,} thanh cong" if ok else f"Rut {amount:,} that bai (so du khong du?)"
            app.post_event("log", f"[TX] {msg}")
        except Exception as e:
            app.post_event("log", f"[TX] Loi giao dich: {e}")
        finally:
            # Xoa don TRUOC khi tra khoa: neu tra khoa ma don con nguyen thi coordinator
            # tu cap quyen cho chinh minh se chay lai dung giao dich vua xong.
            with pending_lock:
                pending_op["type"] = None
                pending_op["amount"] = 0
                pending_op["sent_at"] = None
            release_lock()
            refresh_balance_async()

    def release_lock():
        """Tra quyen ve coordinator.

        Neu chinh minh la coordinator thi khong the gui cho ban than qua mesh duoc
        (server khong luu ket noi toi chinh no), nen phai giai khoá truc tiep.
        """
        if bully.is_coordinator():
            deliver_next(node_id, bank.release_lock(node_id))
            return
        coord_id = bully.coordinator_id
        if coord_id is None:
            return
        if not server.send(coord_id, {"type": "RELEASE_LOCK", "from": node_id}):
            ui_log("Khong gui duoc RELEASE_LOCK, coordinator co the da tat")

    def deliver_next(holder: int, next_holder):
        """Coordinator sau khi nhan RELEASE_LOCK: cap quyen cho node ke tiep trong hang doi FIFO."""
        app.post_event("log", f"[LOCK] RELEASE_LOCK tu node {holder}, next={next_holder}")
        if next_holder is None:
            return
        grant_to(int(next_holder))

    def grant_to(requester: int):
        if requester == node_id:
            handle_grant_lock()
        else:
            server.send(requester, {"type": "GRANT_LOCK", "from": node_id})

    def handle_grant_lock():
        mark_acknowledged()
        with pending_lock:
            tx_type = pending_op["type"]
            amount = pending_op["amount"]
        if not tx_type:
            app.post_event("log", "[LOCK] Nhan GRANT_LOCK nhung khong co giao dich cho")
            # Phai tra khoa: coordinator dang ghi nhan minh la holder, neu im lang
            # thi hang doi FIFO cua toan khoi ket tran vo han (muc 9c chua chot).
            release_lock()
            return
        app.post_event("log", f"[LOCK] Duoc cap quyen, thuc hien {tx_type} {amount:,}")
        threading.Thread(target=do_transaction, args=(tx_type, amount), daemon=True).start()

    def on_lock_request(requester: int, result: str):
        """Coordinator da quyet xong don xin quyen: bao ket qua cho du la GRANTED hay QUEUED.

        Node xin can biet tin nay de phan biet 'dang xep hang' (coordinator van song)
        voi 'coordinator khong phan hoi' (docs/FLOWS.md muc 2b).
        """
        app.post_event("log", f"[LOCK] REQUEST_LOCK tu node {requester} -> {result}")
        if result == "GRANTED":
            grant_to(requester)
            return
        if requester == node_id:
            mark_acknowledged()
        else:
            server.send(requester, {"type": "LOCK_QUEUED", "from": node_id})

    def request_transaction(tx_type: str, amount: int):
        coord_id = bully.coordinator_id
        if coord_id is None:
            ui_log("Chua xac dinh coordinator, khong the xin quyen")
            return
        with pending_lock:
            if pending_op["type"] is not None:
                ui_log("Dang cho quyen cho giao dich truoc, vui long doi")
                return
            pending_op["type"] = tx_type
            pending_op["amount"] = amount
            pending_op["sent_at"] = time.monotonic()
        # Don dang gui toi coordinator hien tai, nen moc so sanh "doi coordinator"
        # phai la nguoi nay; neu khong vong periodic se huy oan don vua gui sau khi bau lai.
        last_coordinator["id"] = coord_id
        ui_log(f"Gui REQUEST_LOCK toi coordinator {coord_id} ({tx_type} {amount:,})")

        if bully.is_coordinator():
            # Tu xin quyen tu chinh khoá cua minh
            on_lock_request(node_id, bank.request_lock(node_id))
            return
        if not server.send(coord_id, {"type": "REQUEST_LOCK", "from": node_id}):
            ui_log("Khong gui duoc REQUEST_LOCK, bat dau bau chon")
            bully.trigger_election("REQUEST_LOCK_SEND_FAIL")

    def check_pending_timeout():
        """Phat hien coordinator loi theo cach thu hai cua FLOWS muc 2b: REQUEST_LOCK vo sinh."""
        with pending_lock:
            waiting = pending_op["type"] is not None
            sent_at = pending_op["sent_at"]
        if not waiting or sent_at is None:
            return
        if time.monotonic() - sent_at < LOCK_REPLY_TIMEOUT:
            return
        mark_acknowledged()  # chi bao mot lan cho don vi tri nay, tranh spam log
        ui_log("Dieu phoi vien khong phan hoi, bat dau bau chon")
        bully.trigger_election("REQUEST_LOCK_NO_REPLY")

    # Gan callback UI
    app.on_deposit = lambda amt: request_transaction("DEPOSIT", amt)
    app.on_withdraw = lambda amt: request_transaction("WITHDRAW", amt)

    # Handler message tu network
    def on_message(nid, message, peer_id, conn):
        msg_type = message.get("type")
        was_coordinator = bully.is_coordinator()

        if msg_type in BULLY_MESSAGES:
            bully.handle_message(nid, message, peer_id, conn)
            if was_coordinator and not bully.is_coordinator():
                # Ha vai tro: hang doi khoa thuoc ve minh thi khong ai con tra loi duoc nua
                # (FLOWS muc 3). Node len thay the tu dong bat dau voi hang doi rong.
                bank.reset_lock()
                ui_log("[LOCK] Xoa trang thai khoa vi khong con la coordinator")
            update_status()
            return

        if msg_type == "REQUEST_LOCK" and bully.is_coordinator():
            requester = message.get("from")
            if requester is not None:
                on_lock_request(int(requester), bank.request_lock(int(requester)))
            return

        if msg_type == "RELEASE_LOCK" and bully.is_coordinator():
            holder = message.get("from")
            if holder is not None:
                deliver_next(int(holder), bank.release_lock(int(holder)))
            return

        if msg_type == "GRANT_LOCK":
            handle_grant_lock()
            return

        if msg_type == "LOCK_QUEUED":
            mark_acknowledged()
            ui_log(f"[LOCK] Coordinator {message.get('from')} da nhan don, dang xep hang cho quyen")
            return

    server.set_message_handler(on_message)
    bully.start()

    # Ket noi mesh sau khi server san sang
    def connect_peers():
        time.sleep(0.5)
        for n in config.get("nodes", []):
            peer_id = int(n.get("id"))
            if peer_id != node_id:
                server.connect_to_peer(peer_id)
        time.sleep(0.5)
        update_status()
        refresh_balance_async()

    threading.Thread(target=connect_peers, daemon=True).start()

    # Cap nhat trang thai dinh ky
    def periodic_update():
        while True:
            time.sleep(2)
            try:
                update_status()
                check_pending_timeout()
            except Exception as exc:
                log_event(node_id, "PERIODIC_ERROR", str(exc))

    threading.Thread(target=periodic_update, daemon=True).start()

    def on_close():
        ui_log("Dang dung node...")
        try:
            bully.stop()
            server.stop()
            bank.close()
        except Exception as exc:
            log_event(node_id, "STOP_ERROR", str(exc))
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()

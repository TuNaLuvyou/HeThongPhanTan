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

    # UI (luong chinh)
    root = tk.Tk()
    app = AppWindow(root, node_id)
    app.post_event("log", f"Node {node_id} khoi dong tai {host}:{port}")

    # Dich vu nen
    server = start_node(node_id)
    bank = BankService()
    bully = Bully(server, node_id)

    # Trang thai giao dich dang cho (client side)
    pending_lock = threading.Lock()
    pending_op = {"type": None, "amount": 0}  # type: "DEPOSIT" | "WITHDRAW" | None

    def ui_log(msg: str):
        log_event(node_id, "UI", msg)
        app.post_event("log", msg)

    def update_status():
        role = "Coordinator" if bully.is_coordinator() else "Node binh thuong"
        coord = bully.coordinator_id if bully.coordinator_id is not None else "chua xac dinh"
        app.post_event("role", role)
        app.post_event("coordinator", str(coord))

    def refresh_balance_async():
        def _job():
            try:
                bal = bank.get_balance()
                app.post_event("balance", f"{bal:,}")
            except Exception as e:
                app.post_event("log", f"[DB] Loi doc so du: {e}")
        threading.Thread(target=_job, daemon=True).start()

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
            # Tra khoa
            coord_id = bully.coordinator_id
            if coord_id is not None:
                server.send(coord_id, {"type": "RELEASE_LOCK", "from": node_id})
            with pending_lock:
                pending_op["type"] = None
                pending_op["amount"] = 0
            refresh_balance_async()

    def handle_grant_lock():
        with pending_lock:
            tx_type = pending_op["type"]
            amount = pending_op["amount"]
        if not tx_type:
            app.post_event("log", "[LOCK] Nhan GRANT_LOCK nhung khong co giao dich cho")
            return
        app.post_event("log", f"[LOCK] Duoc cap quyen, thuc hien {tx_type} {amount:,}")
        threading.Thread(target=do_transaction, args=(tx_type, amount), daemon=True).start()

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
        ui_log(f"Gui REQUEST_LOCK toi coordinator {coord_id} ({tx_type} {amount:,})")

        if bully.is_coordinator():
            # Tu cap quyen cho minh neu la coordinator
            result = bank.request_lock(node_id)
            if result == "GRANTED":
                handle_grant_lock()
            else:
                ui_log("Yeu cau bi xep hang (QUEUE)")
        else:
            server.send(coord_id, {"type": "REQUEST_LOCK", "from": node_id})

    # Gan callback UI
    app.on_deposit = lambda amt: request_transaction("DEPOSIT", amt)
    app.on_withdraw = lambda amt: request_transaction("WITHDRAW", amt)

    # Handler message tu network
    def on_message(nid, message, peer_id, conn):
        msg_type = message.get("type")
        if msg_type in ("ELECTION", "ANSWER", "COORDINATOR", "HEARTBEAT", "HEARTBEAT_ACK"):
            bully.handle_message(nid, message, peer_id, conn)
            update_status()
            return

        if msg_type == "REQUEST_LOCK" and bully.is_coordinator():
            requester = message.get("from")
            if requester is not None:
                result = bank.request_lock(int(requester))
                app.post_event("log", f"[LOCK] REQUEST_LOCK tu node {requester} -> {result}")
                if result == "GRANTED":
                    server.send(int(requester), {"type": "GRANT_LOCK", "from": node_id})
            return

        if msg_type == "RELEASE_LOCK" and bully.is_coordinator():
            holder = message.get("from")
            if holder is not None:
                next_holder = bank.release_lock(int(holder))
                app.post_event("log", f"[LOCK] RELEASE_LOCK tu node {holder}, next={next_holder}")
                if next_holder is not None:
                    if next_holder == node_id:
                        handle_grant_lock()
                    else:
                        server.send(int(next_holder), {"type": "GRANT_LOCK", "from": node_id})
            return

        if msg_type == "GRANT_LOCK":
            handle_grant_lock()
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
            except Exception:
                pass

    threading.Thread(target=periodic_update, daemon=True).start()

    def on_close():
        ui_log("Dang dung node...")
        try:
            bully.stop()
            server.stop()
            bank.close()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()

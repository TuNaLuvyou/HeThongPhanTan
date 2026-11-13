# ui/app_window.py - Thanh vien 5 (Windows 1) - Giao dien Tkinter: header trang thai tu cap nhat, History Log, Quy chung va Nap/Rut
# Doc tham chieu: docs/BUSINESS.md (muc 4), docs/RULES.md (muc 5)

import queue
import tkinter as tk
from tkinter import messagebox, scrolledtext
from typing import Callable, Optional


class AppWindow:
    """Cua so giao dien chinh cho mot node.

    - Header: hien thi vai tro (Node/Coordinator), ID coordinator hien tai.
    - History Log: hien thi tien trinh bau chon, xin/cap/tra quyen, giao dich.
    - Quy chung va Nap/Rut: nhap so tien, nut nap/rut.
    - Cap nhat UI tu luong chinh bang queue + after (RULES muc 5).
    """

    def __init__(self, root: tk.Tk, node_id: int):
        self.root = root
        self.node_id = node_id
        self.root.title(f"Quy chung phan tan - Node {node_id}")
        self._event_queue: queue.Queue = queue.Queue()

        # Callbacks do main/tich hop gan vao
        self.on_deposit: Optional[Callable[[int], None]] = None
        self.on_withdraw: Optional[Callable[[int], None]] = None

        self._build_ui()
        self._poll_queue()

    def _build_ui(self) -> None:
        # Header trang thai
        header = tk.Frame(self.root, padx=8, pady=8)
        header.pack(fill=tk.X)
        self.role_var = tk.StringVar(value="Vai tro: Node binh thuong")
        self.coordinator_var = tk.StringVar(value="Coordinator: chua xac dinh")
        self.balance_var = tk.StringVar(value="So du: -")
        tk.Label(header, textvariable=self.role_var, font=("Arial", 11, "bold")).pack(anchor="w")
        tk.Label(header, textvariable=self.coordinator_var).pack(anchor="w")
        tk.Label(header, textvariable=self.balance_var).pack(anchor="w")

        # Khu vuc Nap/Rut
        action_frame = tk.LabelFrame(self.root, text="Quy chung - Nap/Rut", padx=8, pady=8)
        action_frame.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(action_frame, text="So tien:").grid(row=0, column=0, sticky="w")
        self.amount_entry = tk.Entry(action_frame, width=20)
        self.amount_entry.grid(row=0, column=1, padx=4)
        tk.Button(action_frame, text="Nap tien", command=self._handle_deposit).grid(row=0, column=2, padx=4)
        tk.Button(action_frame, text="Rut tien", command=self._handle_withdraw).grid(row=0, column=3, padx=4)

        # History Log
        log_frame = tk.LabelFrame(self.root, text="History Log", padx=8, pady=8)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=18, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # ----------------------------------------------------------- UI updates (thread-safe)

    def post_event(self, kind: str, payload=None) -> None:
        """Day su kien tu thread khac vao queue de cap nhat tren luong chinh."""
        self._event_queue.put((kind, payload))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._event_queue.get_nowait()
                self._apply_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _apply_event(self, kind: str, payload) -> None:
        if kind == "log":
            self._append_log(str(payload))
        elif kind == "role":
            self.role_var.set(f"Vai tro: {payload}")
        elif kind == "coordinator":
            self.coordinator_var.set(f"Coordinator: {payload}")
        elif kind == "balance":
            self.balance_var.set(f"So du: {payload}")

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ----------------------------------------------------------- Actions

    def _get_amount(self) -> Optional[int]:
        text = self.amount_entry.get().strip()
        if not text.isdigit():
            messagebox.showerror("Loi", "So tien phai la so nguyen duong.")
            return None
        amount = int(text)
        if amount <= 0:
            messagebox.showerror("Loi", "So tien phai la so nguyen duong.")
            return None
        return amount

    def _handle_deposit(self) -> None:
        amount = self._get_amount()
        if amount is None:
            return
        if self.on_deposit:
            self.on_deposit(amount)
        else:
            self._append_log(f"[UI] Yeu cau nap {amount} (chua tich hop)")

    def _handle_withdraw(self) -> None:
        amount = self._get_amount()
        if amount is None:
            return
        if self.on_withdraw:
            self.on_withdraw(amount)
        else:
            self._append_log(f"[UI] Yeu cau rut {amount} (chua tich hop)")


def run_standalone(node_id: int = 0) -> None:
    """Chay UI doc lap de kiem thu giao dien."""
    root = tk.Tk()
    app = AppWindow(root, node_id)
    app.post_event("log", f"UI khoi dong cho node {node_id}")
    app.post_event("role", "Node binh thuong")
    app.post_event("coordinator", "chua xac dinh")
    app.post_event("balance", "10,000,000")
    root.mainloop()


if __name__ == "__main__":
    run_standalone(0)

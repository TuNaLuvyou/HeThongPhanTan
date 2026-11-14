# ui/app_window.py - Thành viên 5 (Windows 1)
"""Giao diện Tkinter của một node.

Cửa sổ gồm ba phần:
- Header    : vai trò hiện tại, ID coordinator, số dư quỹ.
- Nạp/Rút   : ô nhập số tiền và hai nút thao tác.
- Nhật ký hoạt động: nhật ký bầu chọn, xin/cấp/trả quyền và giao dịch.

Lưu ý về thread: Tkinter chỉ được cập nhật từ luồng chính. Các luồng mạng
hoặc luồng nền không gọi trực tiếp widget, mà đẩy sự kiện vào `_event_queue`
bằng post_event(); luồng chính lấy ra và vẽ bằng after() (RULES mục 5).

Tài liệu tham chiếu: docs/BUSINESS.md (mục 4), docs/RULES.md (mục 5).
"""

import queue
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Callable, Optional


class AppWindow:
    """Cửa sổ giao diện chính của một node."""

    def __init__(self, root: tk.Tk, node_id: int):
        self.root = root
        self.node_id = node_id
        self.root.title(f"Quỹ chung phân tán - Nút {node_id}")
        self.root.geometry("900x740")
        self.root.minsize(760, 640)
        self.root.configure(background="#F5F6F8")
        self._font = tkfont.nametofont("TkDefaultFont").actual("family")
        self._log_count = 0

        # Hàng đợi sự kiện từ các luồng khác gửi về luồng giao diện.
        self._event_queue: queue.Queue = queue.Queue()

        # Callback do main.py gắn vào sau khi khởi tạo.
        self.on_deposit: Optional[Callable[[int], None]] = None
        self.on_withdraw: Optional[Callable[[int], None]] = None

        self._build_ui()
        self._poll_queue()

    # ------------------------------------------------------------------
    # Dựng giao diện
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Tạo toàn bộ widget của cửa sổ."""
        style = ttk.Style(self.root)
        # Clam cho phép nút và ô nhập có cùng màu trên Windows và macOS.
        style.theme_use("clam")
        style.configure(
            "Fund.TButton", font=(self._font, 11), padding=(20, 11),
            background="#FFFFFF", foreground="#263449", borderwidth=1,
            bordercolor="#DCE2E8", focuscolor="#FFFFFF", relief="flat",
        )
        style.map(
            "Fund.TButton", background=[("active", "#EFF3F6"), ("pressed", "#E4EAF0")],
        )
        style.configure(
            "Deposit.Fund.TButton", background="#176B54", foreground="#FFFFFF",
            bordercolor="#176B54", focuscolor="#176B54",
        )
        style.map(
            "Deposit.Fund.TButton",
            background=[("pressed", "#104B3B"), ("active", "#125C48")],
            foreground=[("active", "#FFFFFF")],
        )
        style.configure(
            "Fund.TEntry", font=(self._font, 14), padding=(12, 10),
            fieldbackground="#FFFFFF", foreground="#172B3A",
            bordercolor="#DCE2E8", lightcolor="#DCE2E8", darkcolor="#DCE2E8",
        )
        style.map("Fund.TEntry", bordercolor=[("focus", "#176B54")])
        style.configure(
            "Fund.Vertical.TScrollbar", background="#DCE2E8", troughcolor="#FFFFFF",
            borderwidth=0, arrowsize=12,
        )

        self.content = tk.Frame(self.root, background="#F5F6F8", padx=28, pady=24)
        self.content.pack(fill=tk.BOTH, expand=True)
        self._build_header()
        self._build_action_area()
        self._build_log_area()

    def _label(self, parent, text="", size=11, color="#172B3A", bold=False, **kwargs):
        """Dùng chung kiểu chữ và màu nền cho các nhãn trong ứng dụng."""
        return tk.Label(
            parent, text=text, font=(self._font, size, "bold" if bold else "normal"),
            foreground=color, background=parent.cget("background"),
            anchor="w", **kwargs,
        )

    def _card(self, parent):
        return tk.Frame(
            parent, background="#FFFFFF", padx=22, pady=20,
            highlightbackground="#E2E7EC", highlightthickness=1,
        )

    def _build_header(self) -> None:
        """Phần đầu cửa sổ: vai trò, coordinator, số dư."""
        header = tk.Frame(self.content, background="#F5F6F8")
        header.pack(fill=tk.X, pady=(0, 22))
        heading = tk.Frame(header, background="#F5F6F8")
        heading.pack(side=tk.LEFT)
        self._label(heading, "Quỹ chung", size=24, bold=True).pack(anchor="w")
        self._label(heading, "Hệ thống quỹ phân tán", color="#697586").pack(anchor="w", pady=(4, 0))
        tk.Label(
            header, text=f"NÚT {self.node_id:02d}", background="#E8EDF2",
            foreground="#435469", font=(self._font, 10, "bold"), padx=14, pady=8,
        ).pack(side=tk.RIGHT)

        overview = tk.Frame(self.content, background="#F5F6F8")
        overview.pack(fill=tk.X, pady=(0, 16))
        overview.columnconfigure(0, weight=3, uniform="overview")
        overview.columnconfigure(1, weight=2, uniform="overview")

        balance_card = self._card(overview)
        balance_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._label(balance_card, "SỐ DƯ QUỸ", size=10, color="#697586", bold=True).pack(anchor="w")
        balance_row = tk.Frame(balance_card, background="#FFFFFF")
        balance_row.pack(fill=tk.X, pady=(10, 8))
        self.balance_var = tk.StringVar(value="—")
        self._label(
            balance_row, size=32, bold=True, color="#176B54", textvariable=self.balance_var,
        ).pack(side=tk.LEFT)
        self._label(balance_row, "VND", size=10, color="#697586").pack(side=tk.LEFT, padx=(8, 0), anchor="s", pady=7)
        self.updated_var = tk.StringVar(value="Chưa nhận dữ liệu số dư")
        self._label(balance_card, size=10, color="#697586", textvariable=self.updated_var).pack(anchor="w")

        status_card = self._card(overview)
        status_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._label(status_card, "VAI TRÒ HIỆN TẠI", size=10, color="#697586", bold=True).pack(anchor="w")
        self.role_var = tk.StringVar(value="Nút bình thường")
        self.role_label = self._label(status_card, size=15, bold=True, textvariable=self.role_var)
        self.role_label.pack(anchor="w", pady=(12, 14))
        self.coordinator_var = tk.StringVar(value="Điều phối viên: chưa xác định")
        self._label(status_card, size=10, color="#697586", textvariable=self.coordinator_var).pack(anchor="w")

    def _build_action_area(self) -> None:
        """Khu vực nhập số tiền và bấm Nạp/Rút."""
        action_frame = self._card(self.content)
        action_frame.pack(fill=tk.X, pady=(0, 22))
        self._label(action_frame, "Giao dịch", size=14, bold=True).pack(anchor="w")
        self._label(action_frame, "Số tiền (VND)", size=10, color="#697586").pack(anchor="w", pady=(14, 7))
        controls = tk.Frame(action_frame, background="#FFFFFF")
        controls.pack(fill=tk.X)
        controls.columnconfigure(0, weight=1)
        self.amount_entry = ttk.Entry(controls, style="Fund.TEntry", width=16, font=(self._font, 14))
        self.amount_entry.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        ttk.Button(
            controls, text="Nạp tiền", style="Deposit.Fund.TButton", command=self._handle_deposit,
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            controls, text="Rút tiền", style="Fund.TButton", command=self._handle_withdraw,
        ).grid(row=0, column=2)
        self._label(
            action_frame, "Nhập số nguyên dương, ví dụ: 100000", size=10, color="#697586",
        ).pack(anchor="w", pady=(9, 0))

    def _build_log_area(self) -> None:
        """Khu vực Nhật ký hoạt động."""
        log_header = tk.Frame(self.content, background="#F5F6F8")
        log_header.pack(fill=tk.X, pady=(0, 10))
        self._label(log_header, "Nhật ký hoạt động", size=13, bold=True).pack(side=tk.LEFT)
        self.log_count_var = tk.StringVar(value="0 sự kiện")
        self._label(log_header, size=10, color="#697586", textvariable=self.log_count_var).pack(side=tk.RIGHT)

        log_frame = tk.Frame(
            self.content, background="#FFFFFF", highlightbackground="#E2E7EC", highlightthickness=1,
        )
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(
            log_frame, height=9, state=tk.DISABLED, wrap=tk.WORD,
            background="#FFFFFF", foreground="#435469", font=(self._font, 10),
            borderwidth=0, highlightthickness=0, padx=16, pady=12,
            spacing1=3, spacing3=7, selectbackground="#DDEDE7", selectforeground="#172B3A",
        )
        scrollbar = ttk.Scrollbar(
            log_frame, orient=tk.VERTICAL, command=self.log_text.yview, style="Fund.Vertical.TScrollbar",
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("time", foreground="#8993A1")
        self.log_text.tag_configure("success", foreground="#176B54")
        self.log_text.tag_configure("error", foreground="#B54738")

    # ------------------------------------------------------------------
    # Nhận sự kiện từ luồng khác (thread-safe)
    # ------------------------------------------------------------------

    def post_event(self, kind: str, payload=None) -> None:
        """Đẩy một sự kiện vào hàng đợi để luồng chính xử lý sau.

        Các loại sự kiện đang dùng:
            "log"         - thêm một dòng vào Nhật ký hoạt động.
            "role"        - cập nhật vai trò (Node/Coordinator).
            "coordinator" - cập nhật ID coordinator.
            "balance"     - cập nhật số dư.
        """
        self._event_queue.put((kind, payload))

    def _poll_queue(self) -> None:
        """Lấy hết sự kiện đang chờ và vẽ lên giao diện, rồi hẹn lần sau."""
        try:
            while True:
                kind, payload = self._event_queue.get_nowait()
                self._apply_event(kind, payload)
        except queue.Empty:
            pass

        self.root.after(100, self._poll_queue)

    def _apply_event(self, kind: str, payload) -> None:
        """Áp dụng một sự kiện lên widget tương ứng."""
        if kind == "log":
            self._append_log(str(payload))
        elif kind == "role":
            self.role_var.set(str(payload))
            self.role_label.configure(foreground="#176B54" if payload == "Điều phối viên" else "#172B3A")
        elif kind == "coordinator":
            self.coordinator_var.set(f"Điều phối viên: {payload}")
        elif kind == "balance":
            self.balance_var.set(str(payload))
            self.updated_var.set(f"Cập nhật lúc {datetime.now():%H:%M:%S}")

    def _append_log(self, line: str) -> None:
        """Thêm một dòng vào cuối Nhật ký hoạt động."""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{datetime.now():%H:%M:%S}   ", "time")
        lowered = line.lower()
        tag = "error" if any(word in lowered for word in ("lỗi", "thất bại", "không đủ")) else ""
        if not tag and "thành công" in lowered:
            tag = "success"
        self.log_text.insert(tk.END, line + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self._log_count += 1
        self.log_count_var.set(f"{self._log_count} sự kiện")

    # ------------------------------------------------------------------
    # Thao tác của người dùng
    # ------------------------------------------------------------------

    def _get_amount(self) -> Optional[int]:
        """Đọc số tiền từ ô nhập. Trả về None nếu không hợp lệ."""
        text = self.amount_entry.get().strip()

        if not text.isdigit():
            messagebox.showerror("Lỗi", "Số tiền phải là số nguyên dương.")
            return None

        amount = int(text)
        if amount <= 0:
            messagebox.showerror("Lỗi", "Số tiền phải là số nguyên dương.")
            return None

        return amount

    def _handle_deposit(self) -> None:
        """Xử lý khi bấm nút Nạp tiền."""
        amount = self._get_amount()
        if amount is None:
            return

        if self.on_deposit is None:
            self._append_log(f"[Giao diện] Yêu cầu nạp {amount} (chưa tích hợp)")
            return

        self.on_deposit(amount)

    def _handle_withdraw(self) -> None:
        """Xử lý khi bấm nút Rút tiền."""
        amount = self._get_amount()
        if amount is None:
            return

        if self.on_withdraw is None:
            self._append_log(f"[Giao diện] Yêu cầu rút {amount} (chưa tích hợp)")
            return

        self.on_withdraw(amount)


def run_standalone(node_id: int = 0) -> None:
    """Chạy riêng giao diện để kiểm thử mà không cần mạng hay DB."""
    root = tk.Tk()
    app = AppWindow(root, node_id)

    app.post_event("log", f"Giao diện khởi động cho nút {node_id}")
    app.post_event("role", "Nút bình thường")
    app.post_event("coordinator", "chưa xác định")
    app.post_event("balance", "10,000,000")

    root.mainloop()


if __name__ == "__main__":
    run_standalone(0)

# core/network.py - Thành viên 3 (Linux 1)
"""Tầng mạng của một node.

Mỗi node vừa là server (lắng nghe kết nối đến) vừa là client (chủ động nối
tới các node khác). Giao thức truyền tin:

    Mỗi gói tin là một chuỗi JSON trên một dòng, kết thúc bằng ký tự '\n'.

File này chỉ lo việc gửi/nhận byte và đóng gói JSON. Việc hiểu nội dung gói
tin (ELECTION, REQUEST_LOCK, ...) thuộc về core/bully.py và main.py.

Quy ước định danh node gửi: lấy trường `from` trong gói tin, KHÔNG suy ra từ
địa chỉ IP. Nhiều node có thể chạy chung một máy nên IP bị trùng nhau.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Hằng số dùng chung
# ---------------------------------------------------------------------------

CONFIG_PATH = "config.json"
LOG_DIR = "logs"
DEFAULT_HOST = "0.0.0.0"
MESSAGE_DELIMITER = b"\n"
BUFFER_SIZE = 4096

# Terminal Windows hoặc đầu ra chuyển hướng có thể mặc định dùng cp1252.
# Dùng UTF-8 để log tiếng Việt không làm dừng luồng mạng.
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Đọc cấu hình
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Đọc toàn bộ file config.json của dự án."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def get_node_config(node_id: int) -> dict:
    """Trả về phần cấu hình (ip, port, os) của một node theo id."""
    config = load_config()
    for node_config in config.get("nodes", []):
        if node_config.get("id") == node_id:
            return node_config
    raise ValueError(f"Không tìm thấy nút {node_id} trong {CONFIG_PATH}")


def get_host_by_id(node_id: int) -> str:
    """Trả về địa chỉ IP của node theo id."""
    node_config = get_node_config(node_id)
    return node_config.get("ip", DEFAULT_HOST)


def get_port_by_id(node_id: int) -> int:
    """Trả về port của node theo id, mặc định tăng dần theo id."""
    node_config = get_node_config(node_id)
    return int(node_config.get("port", 3000 + node_id))


# ---------------------------------------------------------------------------
# Ghi log
# ---------------------------------------------------------------------------

EVENT_LABELS = {
    "NODE_START": "Khởi động nút",
    "NODE_READY": "Nút sẵn sàng",
    "NODE_START_FAILED": "Khởi động nút thất bại",
    "SERVER_STARTED": "Đã mở cổng lắng nghe",
    "SERVER_FAILED": "Không mở được cổng lắng nghe",
    "CLIENT_CONNECTED": "Đã kết nối",
    "CONNECT_FAILED": "Kết nối thất bại",
    "HANDLER_ERROR": "Lỗi xử lý gói tin",
    "BULLY_START": "Khởi động bầu chọn Bully",
    "BULLY_STOP": "Dừng bầu chọn Bully",
    "ELECTION_START": "Bắt đầu bầu chọn",
    "ELECTION_SEND_FAIL": "Không gửi được yêu cầu bầu chọn",
    "ELECTION_YIELD": "Nhường cho nút có ID cao hơn",
    "BECOME_COORDINATOR": "Trở thành điều phối viên",
    "PEERS_ALIVE": "Các nút đang phản hồi",
    "COORDINATOR_FAIL": "Điều phối viên không phản hồi",
    "NO_COORDINATOR": "Chưa có điều phối viên",
    "IGNORE_AFTER_STOP": "Bỏ qua thông báo sau khi dừng",
    "STEP_DOWN": "Nhường vai trò điều phối viên",
    "FOLLOW_COORDINATOR": "Đã nhận điều phối viên",
    "LOCK_GRANTED": "Đã cấp quyền",
    "LOCK_QUEUED": "Đang xếp hàng chờ quyền",
    "LOCK_RESET": "Đã xoá trạng thái khoá",
    "DEPOSIT_INVALID": "Số tiền nạp không hợp lệ",
    "DEPOSIT_OK": "Nạp tiền thành công",
    "WITHDRAW_INVALID": "Số tiền rút không hợp lệ",
    "WITHDRAW_REJECT": "Từ chối rút vì không đủ số dư",
    "WITHDRAW_OK": "Rút tiền thành công",
    "STOP_ERROR": "Lỗi khi dừng nút",
    "PERIODIC_ERROR": "Lỗi cập nhật trạng thái",
    "UI": "Thông báo",
}


def log_event(node_id: int, event: str, detail: str = "") -> None:
    """Ghi một sự kiện ra màn hình và ra file logs/node_<id>.log.

    Định dạng mỗi dòng:
        [thời gian] node <id> | <tên sự kiện> | <chi tiết>
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    event_text = EVENT_LABELS.get(event, event)
    line = f"[{timestamp}] Nút {node_id} | {event_text} | {detail}"
    print(line, flush=True)

    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir / f"node_{node_id}.log"
    with log_file_path.open("a", encoding="utf-8") as log_file:
        log_file.write(line + "\n")


# ---------------------------------------------------------------------------
# Một kết nối TCP
# ---------------------------------------------------------------------------

class ClientConnection:
    """Một kết nối TCP tới một peer.

    Dữ liệu nhận về được tích vào `_buffer` cho tới khi gặp ký tự '\n' thì
    mới tách ra thành một gói tin hoàn chỉnh. Nhờ vậy một gói tin có thể đến
    từ nhiều lần recv(), hoặc nhiều gói tin có thể đến chung một lần recv().
    """

    def __init__(self, addr: tuple, timeout: float = 5.0):
        self.addr = addr
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._buffer = b""
        self._closed = False
        self._send_lock = threading.Lock()

    def attach(self, sock: socket.socket) -> None:
        """Gắn socket có sẵn vào kết nối (dùng cho kết nối do server accept)."""
        self._socket = sock

    def connect(self, host: str, port: int) -> bool:
        """Chủ động mở kết nối tới host:port. Trả về False nếu thất bại."""
        try:
            self._socket = socket.create_connection((host, port), timeout=self.timeout)
            self._socket.settimeout(self.timeout)
            return True
        except OSError:
            self._socket = None
            return False

    def close(self) -> None:
        """Đánh dấu đã đóng và giải phóng socket."""
        self._closed = True
        if self._socket is None:
            return
        try:
            self._socket.close()
        except OSError:
            pass
        self._socket = None

    def send(self, payload: dict) -> bool:
        """Gửi một dict dưới dạng JSON kèm '\n'. Trả về False nếu lỗi."""
        if self._closed or self._socket is None:
            return False

        try:
            json_text = json.dumps(payload)
            data = (json_text + "\n").encode("utf-8")
            # Heartbeat và thông báo số dư có thể được gửi từ hai thread.
            with self._send_lock:
                sock = self._socket
                if self._closed or sock is None:
                    return False
                sock.sendall(data)
            return True
        except OSError:
            self.close()
            return False

    def receive(self) -> Optional[dict]:
        """Nhận một gói tin JSON.

        Khi hết timeout tiếp tục chờ; trả về None nếu lỗi hoặc kết nối đóng.
        """
        if self._closed or self._socket is None:
            return None

        # Đọc thêm dữ liệu cho tới khi có ít nhất một dòng hoàn chỉnh.
        while MESSAGE_DELIMITER not in self._buffer:
            sock = self._socket
            if self._closed or sock is None:
                return None

            try:
                sock.settimeout(self.timeout)
                chunk = sock.recv(BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError:
                return None

            if not chunk:
                # Peer đã đóng kết nối.
                return None

            self._buffer += chunk

        # Tách lấy một dòng, phần còn lại giữ trong buffer cho lần sau.
        raw_line, self._buffer = self._buffer.split(MESSAGE_DELIMITER, 1)
        raw_line = raw_line.strip()
        if not raw_line:
            return None

        try:
            return json.loads(raw_line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


# ---------------------------------------------------------------------------
# Server + client của một node
# ---------------------------------------------------------------------------

class NetworkServer:
    """Quản lý toàn bộ hoạt động mạng của một node.

    - Lắng nghe kết nối đến trên (host, port) của node.
    - Giữ một ClientConnection cho mỗi peer mà node chủ động nối tới.
    - Chuyển gói tin nhận được cho hàm xử lý đã đăng ký.
    """

    def __init__(self, node_id: int, host: str, port: int, timeout: float = 5.0):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self._running = False
        self._server_socket = None
        self._clients: dict = {}
        self._connections: set = set()
        self._lock = threading.Lock()
        self._handler = None

    def set_message_handler(self, handler: Callable) -> None:
        """Đăng ký hàm xử lý gói tin.

        Hàm sẽ được gọi với bốn tham số: (node_id, message, peer_id, conn).
        - node_id : id của node đang nhận.
        - message : dict JSON vừa nhận.
        - peer_id : id node gửi, lấy từ trường `from` (None nếu không có).
        - conn    : ClientConnection dùng để nhận gói tin này.
        """
        self._handler = handler

    def start(self) -> bool:
        """Bind port và bắt đầu lắng nghe. Trả về False nếu bind thất bại."""
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((self.host, self.port))
            server_socket.listen(10)
            server_socket.settimeout(1.0)

            self._server_socket = server_socket
            self._running = True

            log_event(self.node_id, "SERVER_STARTED", f"Địa chỉ: {self.host}:{self.port}")

            accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            accept_thread.start()
            return True
        except OSError as exc:
            log_event(self.node_id, "SERVER_FAILED", str(exc))
            return False

    def stop(self) -> None:
        """Dừng server và đóng toàn bộ kết nối client."""
        self._running = False

        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass

        with self._lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()
            self._clients.clear()

    def connect_to_peer(self, peer_id: int) -> bool:
        """Chủ động mở kết nối tới một peer và bắt đầu lắng nghe phản hồi."""
        peer_config = get_node_config(peer_id)
        host = peer_config.get("ip", DEFAULT_HOST)
        port = int(peer_config.get("port", 3000 + peer_id))

        conn = ClientConnection((host, port), timeout=self.timeout)
        if not conn.connect(host, port):
            log_event(self.node_id, "CONNECT_FAILED", f"Không kết nối được tới nút {peer_id}")
            return False

        with self._lock:
            if not self._running:
                conn.close()
                return False
            self._clients[peer_id] = conn
            self._connections.add(conn)

        listen_thread = threading.Thread(
            target=self._listen_loop,
            args=(peer_id, conn),
            daemon=True,
        )
        listen_thread.start()
        log_event(self.node_id, "CLIENT_CONNECTED", f"Kết nối tới nút {peer_id}")
        return True

    def send(self, peer_id: int, payload: dict) -> bool:
        """Gửi một gói tin tới peer theo id. Trả về False nếu không gửi được."""
        with self._lock:
            conn = self._clients.get(peer_id)

        if conn is None:
            return False

        sent = conn.send(payload)
        if not sent:
            # Kết nối đã chết: bỏ khỏi danh sách để lần sau không dùng lại.
            with self._lock:
                self._clients.pop(peer_id, None)
        return sent

    # -- Vòng lặp nội bộ ----------------------------------------------------

    def _accept_loop(self) -> None:
        """Chờ và nhận kết nối mới từ các node khác."""
        while self._running:
            try:
                self._server_socket.settimeout(1.0)
                sock, addr = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            conn = ClientConnection(addr, timeout=self.timeout)
            conn.attach(sock)
            with self._lock:
                if not self._running:
                    conn.close()
                    break
                self._connections.add(conn)

            peer_id = self._resolve_peer_id(addr)
            log_event(self.node_id, "CLIENT_CONNECTED", f"Nhận kết nối từ {addr[0]}:{addr[1]}; nút dự đoán: {peer_id}")

            listen_thread = threading.Thread(
                target=self._listen_loop,
                args=(peer_id, conn),
                daemon=True,
            )
            listen_thread.start()

    def _resolve_peer_id(self, addr: tuple) -> Optional[int]:
        """Đoán id của peer dựa trên IP, CHỈ để ghi log.

        Không dùng giá trị này để định danh node gửi: nhiều node chạy chung
        một máy sẽ có cùng IP, còn port nguồn là port ngẫu nhiên do hệ điều
        hành cấp. Định danh thật nằm ở trường `from` của gói tin.
        """
        config = load_config()
        for node_config in config.get("nodes", []):
            if node_config.get("ip") == addr[0]:
                return int(node_config.get("id"))
        return None

    def _listen_loop(self, peer_id: Optional[int], conn: ClientConnection) -> None:
        """Đọc gói tin trên một kết nối và chuyển cho handler.

        peer_id ban đầu có thể là None hoặc đoán từ IP. Khi gói tin có trường
        `from` thì dùng trường đó làm id chính thức của node gửi.
        """
        try:
            while self._running:
                message = conn.receive()
                if message is None:
                    break

                sender_id = message.get("from")
                if sender_id is not None:
                    try:
                        peer_id = int(sender_id)
                        # Máy mở sau chỉ cần kết nối tới máy đang chạy.
                        # Ghi nhận chiều nhận để máy cũ gửi ngược heartbeat,
                        # thông báo điều phối viên và quyền trên cùng socket.
                        with self._lock:
                            if peer_id != self.node_id:
                                self._clients.setdefault(peer_id, conn)
                    except (TypeError, ValueError):
                        pass

                if self._handler is None:
                    continue

                try:
                    self._handler(self.node_id, message, peer_id, conn)
                except Exception as exc:
                    log_event(self.node_id, "HANDLER_ERROR", str(exc))
        finally:
            conn.close()
            with self._lock:
                self._connections.discard(conn)
                # Chỉ xoá đúng kết nối này, không xoá kết nối mới thay thế.
                for client_id, client in list(self._clients.items()):
                    if client is conn:
                        self._clients.pop(client_id, None)


# ---------------------------------------------------------------------------
# Hàm tiện ích để main.py khởi động mạng
# ---------------------------------------------------------------------------

def start_node(node_id: int) -> Optional[NetworkServer]:
    """Tạo và chạy NetworkServer của một node dựa trên config.json.

    Trả về None nếu bind thất bại (port đã bị chiếm, hoặc IP trong config
    không phải IP của máy đang chạy). Người gọi phải dừng chương trình, nếu
    không node sẽ mở giao diện mà không ai kết nối được tới.
    """
    host = get_host_by_id(node_id)
    port = get_port_by_id(node_id)

    server = NetworkServer(node_id=node_id, host=host, port=port)
    if not server.start():
        log_event(node_id, "NODE_START_FAILED", f"Địa chỉ: {host}:{port}")
        return None

    log_event(node_id, "NODE_READY", f"Địa chỉ: {host}:{port}")
    return server

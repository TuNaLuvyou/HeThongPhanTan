# core/network.py - Thanh vien 3 (Linux 1) - Socket TCP server/client cho node
# Doc tham chieu: docs/TECHS.md (muc 2, 5, 6), docs/FLOWS.md, docs/RULES.md

import json
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

CONFIG_PATH = "config.json"
LOG_DIR = "logs"
DEFAULT_HOST = "0.0.0.0"
MESSAGE_DELIMITER = b"\n"


def load_config() -> dict:
    """Doc cau hinh chung cua du an tu file config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_node_config(node_id: int) -> dict:
    """Lay cau hinh (ip, port, os) cua mot node tu config.json."""
    for node in load_config().get("nodes", []):
        if node.get("id") == node_id:
            return node
    raise ValueError(f"Khong tim thay node {node_id} trong {CONFIG_PATH}")


def get_host_by_id(node_id: int) -> str:
    """Lay IP cua node theo id."""
    return get_node_config(node_id).get("ip", DEFAULT_HOST)


def get_port_by_id(node_id: int) -> int:
    """Lay port cua node theo id, mac dinh tang theo node id."""
    return int(get_node_config(node_id).get("port", 3000 + node_id))


def log_event(node_id: int, event: str, detail: str = "") -> None:
    """Ghi su kien: moi su kien gom thoi gian, ID node va su kien."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] node {node_id} | {event} | {detail}"
    print(line, flush=True)
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"node_{node_id}.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


class ClientConnection:
    """Ket noi TCP voi mot peer. JSON message nam tren mot dong, ket thuc bang '\\n'."""

    def __init__(self, addr: tuple, timeout: float = 5.0):
        self.addr = addr
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._buffer = b""
        self._closed = False

    def attach(self, sock: socket.socket) -> None:
        """Gan socket vao connection (dung cho connection nhan tu server accept)."""
        self._socket = sock

    def connect(self, host: str, port: int) -> bool:
        try:
            self._socket = socket.create_connection((host, port), timeout=self.timeout)
            self._socket.settimeout(self.timeout)
            return True
        except OSError:
            self._socket = None
            return False

    def close(self) -> None:
        self._closed = True
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def send(self, payload: dict) -> bool:
        if self._closed or self._socket is None:
            return False
        try:
            data = (json.dumps(payload) + "\n").encode("utf-8")
            self._socket.sendall(data)
            return True
        except OSError:
            self.close()
            return False

    def receive(self) -> Optional[dict]:
        """Nhan JSON message. Tra ve None neu timeout / loi / ket noi dong."""
        if self._closed or self._socket is None:
            return None
        while b"\n" not in self._buffer:
            if self._closed:
                return None
            self._socket.settimeout(self.timeout)
            try:
                chunk = self._socket.recv(4096)
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            self._buffer += chunk
        line, self._buffer = self._buffer.split(MESSAGE_DELIMITER, 1)
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


class NetworkServer:
    """TCP server + client cua mot node. Quan ly ket noi outgoing theo peer id."""

    def __init__(self, node_id: int, host: str, port: int, timeout: float = 5.0):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self._running = False
        self._server_socket = None
        self._clients: dict = {}
        self._lock = threading.Lock()
        self._handler = None

    def set_message_handler(self, handler: Callable) -> None:
        """Cai handler nhan message.

        Handler se duoc goi voi (node_id, message, peer_id, conn):
        - node_id: ID node hien tai (nguoi nhan).
        - message: JSON message da nhan duoc.
        - peer_id: ID peer gui message (None neu khong xac dinh duoc).
        - conn: ClientConnection su dung.
        """
        self._handler = handler

    def start(self) -> bool:
        """Bind cai server socket cua node, cho phe chap lang nghe."""
        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind((self.host, self.port))
            self._server_socket.listen(10)
            self._server_socket.settimeout(1.0)
            self._running = True
            log_event(self.node_id, "SERVER_STARTED", f"host={self.host} port={self.port}")
            threading.Thread(target=self._accept_loop, daemon=True).start()
            return True
        except OSError as exc:
            log_event(self.node_id, "SERVER_FAILED", str(exc))
            return False

    def stop(self) -> None:
        """Dung server va dong tat cac ket noi client."""
        self._running = False
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
        with self._lock:
            for conn in self._clients.values():
                conn.close()
            self._clients.clear()

    def connect_to_peer(self, peer_id: int) -> bool:
        """Ket noi toi peer (dung cho tiep cận trong cau truc mesh 6 node)."""
        peer = get_node_config(peer_id)
        host = peer.get("ip", DEFAULT_HOST)
        port = int(peer.get("port", 3000 + peer_id))
        conn = ClientConnection((host, port), timeout=self.timeout)
        if not conn.connect(host, port):
            log_event(self.node_id, "CONNECT_FAILED", f"peer={peer_id}")
            return False
        with self._lock:
            self._clients[peer_id] = conn
        threading.Thread(target=self._listen_loop, args=(peer_id, conn), daemon=True).start()
        log_event(self.node_id, "CLIENT_CONNECTED", f"peer={peer_id}")
        return True

    def send(self, peer_id: int, payload: dict) -> bool:
        """Gui JSON message toi peer theo peer_id."""
        with self._lock:
            conn = self._clients.get(peer_id)
        if conn is None:
            return False
        ok = conn.send(payload)
        if not ok:
            with self._lock:
                self._clients.pop(peer_id, None)
        return ok

    def _accept_loop(self) -> None:
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
            peer_id = self._resolve_peer_id(addr)
            log_event(self.node_id, "CLIENT_CONNECTED", f"{addr[0]}:{addr[1]} peer={peer_id}")
            threading.Thread(target=self._listen_loop, args=(peer_id, conn), daemon=True).start()

    def _resolve_peer_id(self, addr: tuple) -> Optional[int]:
        """Xac dinh ID peer gom IP cua endpoint nhan."""
        for node in load_config().get("nodes", []):
            if node.get("ip") == addr[0]:
                return int(node.get("id"))
        return None

    def _listen_loop(self, peer_id: Optional[int], conn: ClientConnection) -> None:
        """Vong lang nghe cua mot connection, dispatch message cho handler."""
        while self._running:
            message = conn.receive()
            if message is None:
                break
            if self._handler is not None:
                try:
                    self._handler(self.node_id, message, peer_id, conn)
                except Exception as exc:
                    log_event(self.node_id, "HANDLER_ERROR", str(exc))


def start_node(node_id: int) -> NetworkServer:
    """Tao NetworkServer cua mot node: bind port tu config theo --id."""
    host = get_host_by_id(node_id)
    port = get_port_by_id(node_id)
    server = NetworkServer(node_id=node_id, host=host, port=port)
    ok = server.start()
    if ok:
        log_event(node_id, "NODE_READY", f"host={host} port={port}")
    return server

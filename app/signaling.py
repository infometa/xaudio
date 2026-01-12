import json
import logging
import socket
import threading
import time
import uuid


class Signaling:
    """Minimal HELLO/ACK/KEEPALIVE/BYE signaling over UDP."""

    def __init__(self, on_connected=None, on_disconnected=None, on_incoming=None):
        self.logger = logging.getLogger("Signaling")
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_incoming = on_incoming
        self.sock = None
        self.recv_thread = None
        self.keepalive_thread = None
        self.running = False
        self.state = "idle"
        self.remote_addr = None
        self.local_port = None
        self.call_id = None
        self.tie = None
        self.last_seen = 0.0
        self.lock = threading.Lock()

    def start_listen(self, local_port):
        if self.sock:
            return
        self.local_port = local_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.sock.bind(("0.0.0.0", local_port))
        self.running = True
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()
        self.keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self.keepalive_thread.start()
        self.logger.info("Signaling listening on %s", local_port)

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        
        if self.recv_thread and self.recv_thread.is_alive():
            self.recv_thread.join(timeout=2.0)
        if self.keepalive_thread and self.keepalive_thread.is_alive():
            self.keepalive_thread.join(timeout=2.0)
        
        self.sock = None
        self.state = "idle"
        self.remote_addr = None

    def call(self, remote_ip, remote_port):
        with self.lock:
            self.remote_addr = (remote_ip, remote_port)
            self.call_id = str(uuid.uuid4())
            self.tie = int(uuid.uuid4().int & 0x7FFFFFFF)
            self.state = "calling"
        self.logger.info("Calling %s:%d", remote_ip, remote_port)
        self._send({"type": "HELLO", "call_id": self.call_id, "tie": self.tie})
        if self.on_incoming:
            self.on_incoming(remote_ip, remote_port)

    def hangup(self):
        if self.remote_addr:
            self._send({"type": "BYE"})
        self._set_disconnected("local hangup")

    def _set_connected(self):
        if self.state != "connected":
            self.state = "connected"
            self.logger.info("Call connected to %s:%d", self.remote_addr[0], self.remote_addr[1])
            if self.on_connected:
                self.on_connected(self.remote_addr)

    def _set_disconnected(self, reason):
        with self.lock:
            if self.state != "idle":
                self.logger.info("Disconnected: %s", reason)
            self.state = "idle"
            self.remote_addr = None
            self.call_id = None
        if self.on_disconnected:
            self.on_disconnected()

    def _send(self, payload):
        with self.lock:
            if not self.sock or not self.remote_addr:
                return
            payload = dict(payload)
            payload["ts"] = time.time()
            payload["id"] = self.call_id
            try:
                self.sock.sendto(json.dumps(payload).encode("utf-8"), self.remote_addr)
            except OSError as exc:
                self.logger.warning("Send failed: %s", exc)

    def _recv_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            if msg_type == "HELLO":
                self._handle_hello(msg, addr)
            elif msg_type == "ACK":
                self._handle_ack(msg, addr)
            elif msg_type == "KEEPALIVE":
                self._handle_keepalive(addr)
            elif msg_type == "BYE":
                self._handle_bye(addr)

    def _handle_hello(self, msg, addr):
        remote_tie = int(msg.get("tie", 0))
        with self.lock:
            if self.state == "calling":
                if remote_tie > (self.tie or 0):
                    self.logger.info("Incoming HELLO from %s:%d (tie won, accepting)", addr[0], addr[1])
                    self.remote_addr = addr
                    self.call_id = msg.get("call_id") or self.call_id
                    self._send({"type": "ACK", "call_id": self.call_id})
                    self._set_connected()
                else:
                    self.logger.info("Incoming HELLO from %s:%d (tie lost, rejecting)", addr[0], addr[1])
                    self._send({"type": "BUSY"})
                return
            if self.state == "connected":
                if addr == self.remote_addr:
                    self._send({"type": "ACK", "call_id": self.call_id})
                return
            self.logger.info("Client connected from %s:%d", addr[0], addr[1])
            self.remote_addr = addr
            self.call_id = msg.get("call_id") or str(uuid.uuid4())
            self.tie = int(uuid.uuid4().int & 0x7FFFFFFF)
            self._send({"type": "ACK", "call_id": self.call_id})
            self._set_connected()

    def _handle_ack(self, msg, addr):
        with self.lock:
            if self.state == "calling" and addr == self.remote_addr:
                self._set_connected()

    def _handle_keepalive(self, addr):
        with self.lock:
            if self.remote_addr and addr == self.remote_addr:
                self.last_seen = time.time()

    def _handle_bye(self, addr):
        if self.remote_addr and addr == self.remote_addr:
            self._set_disconnected("remote bye")

    def _keepalive_loop(self):
        while self.running:
            time.sleep(1.0)
            with self.lock:
                if self.state == "connected":
                    self._send({"type": "KEEPALIVE"})
                if self.state == "connected" and self.last_seen:
                    if time.time() - self.last_seen > 3.0:
                        self._set_disconnected("keepalive timeout")

import json
import logging
import os
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
        self.remote_rtp_port = None
        self.local_rtp_port = None
        self.local_port = None
        self.call_id = None
        self.tie = None
        self.last_seen = 0.0
        self.keepalive_misses = 0
        self.lock = threading.RLock()
        self.bind_ip = os.getenv("TCHAT_SIGNAL_BIND", "0.0.0.0").strip() or "0.0.0.0"
        self.token = os.getenv("TCHAT_SIGNAL_TOKEN", "").strip()
        allowlist_raw = os.getenv("TCHAT_SIGNAL_ALLOWLIST", "").strip()
        self.allowlist = {item for item in allowlist_raw.replace(" ", ",").split(",") if item}
        try:
            interval = float(os.getenv("TCHAT_KEEPALIVE_INTERVAL", "1.0"))
        except ValueError:
            interval = 1.0
        try:
            timeout = float(os.getenv("TCHAT_KEEPALIVE_TIMEOUT", "6.0"))
        except ValueError:
            timeout = 6.0
        try:
            misses = int(os.getenv("TCHAT_KEEPALIVE_MAX_MISSES", "5"))
        except ValueError:
            misses = 5
        self.keepalive_interval = max(0.2, interval)
        self.keepalive_timeout = max(1.0, timeout)
        self.keepalive_max_misses = max(1, misses)

    def set_local_rtp_port(self, port):
        try:
            self.local_rtp_port = int(port)
        except (TypeError, ValueError):
            self.local_rtp_port = None

    def start_listen(self, local_port, rtp_port=None):
        if self.sock:
            return
        self.local_port = local_port
        if rtp_port is not None:
            self.set_local_rtp_port(rtp_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            rcvbuf = int(os.getenv("TCHAT_SIGNAL_RCVBUF", "1048576"))
        except ValueError:
            rcvbuf = 1048576
        try:
            sndbuf = int(os.getenv("TCHAT_SIGNAL_SNDBUF", "1048576"))
        except ValueError:
            sndbuf = 1048576
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
        except OSError as exc:
            self.logger.warning("Failed to set UDP buffer size: %s", exc)
        self.sock.settimeout(1.0)
        self.sock.bind((self.bind_ip, local_port))
        self.running = True
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()
        self.keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self.keepalive_thread.start()
        self.logger.info("Signaling listening on %s (%s)", local_port, self.bind_ip)
        if self.bind_ip == "0.0.0.0" and not self.allowlist and not self.token:
            self.logger.warning("Signaling is exposed on all interfaces without allowlist/token")

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
        self.remote_rtp_port = None
        self.keepalive_misses = 0

    def call(self, remote_ip, remote_port):
        with self.lock:
            self.remote_addr = (remote_ip, remote_port)
            self.call_id = str(uuid.uuid4())
            self.tie = int(uuid.uuid4().int & 0x7FFFFFFF)
            self.state = "calling"
        self.logger.info("Calling %s:%d", remote_ip, remote_port)
        self._send({"type": "HELLO", "call_id": self.call_id, "tie": self.tie, "rtp_port": self.local_rtp_port})
        if self.on_incoming:
            self.on_incoming(remote_ip, remote_port)

    def hangup(self):
        if self.remote_addr:
            self._send({"type": "BYE"})
        self._set_disconnected("local hangup")

    def _set_connected(self):
        if self.state != "connected":
            self.state = "connected"
            self.last_seen = time.monotonic()
            self.keepalive_misses = 0
            self.logger.info("Call connected to %s:%d", self.remote_addr[0], self.remote_addr[1])
            if self.on_connected:
                remote_info = (self.remote_addr[0], self.remote_addr[1], self.remote_rtp_port)
                self.on_connected(remote_info)

    def _set_disconnected(self, reason):
        with self.lock:
            if self.state != "idle":
                self.logger.info("Disconnected: %s", reason)
            self.state = "idle"
            self.remote_addr = None
            self.call_id = None
            self.remote_rtp_port = None
            self.keepalive_misses = 0
        if self.on_disconnected:
            self.on_disconnected()

    def _send(self, payload):
        with self.lock:
            if not self.sock or not self.remote_addr:
                return
            payload = dict(payload)
            payload["ts"] = time.time()
            payload["id"] = self.call_id
            if self.token:
                payload["token"] = self.token
            try:
                self.sock.sendto(json.dumps(payload).encode("utf-8"), self.remote_addr)
            except OSError as exc:
                self.logger.warning("Send failed: %s", exc)

    def _mark_seen(self):
        self.last_seen = time.monotonic()
        self.keepalive_misses = 0

    def _recv_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                decoded = data.decode("utf-8")
                msg = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not self._accept_message(msg, addr):
                continue
            msg_type = msg.get("type")
            if msg_type == "HELLO":
                self._handle_hello(msg, addr)
            elif msg_type == "ACK":
                self._handle_ack(msg, addr)
            elif msg_type == "KEEPALIVE":
                self._handle_keepalive(msg, addr)
            elif msg_type == "BYE":
                self._handle_bye(msg, addr)
            elif msg_type == "BUSY":
                self._handle_busy(addr)

    def _handle_hello(self, msg, addr):
        remote_tie = int(msg.get("tie", 0))
        remote_rtp = msg.get("rtp_port")
        with self.lock:
            if self.state == "calling":
                if remote_tie > (self.tie or 0):
                    self.logger.info("Incoming HELLO from %s:%d (tie won, accepting)", addr[0], addr[1])
                    self.remote_addr = addr
                    if remote_rtp is not None:
                        try:
                            self.remote_rtp_port = int(remote_rtp)
                        except (TypeError, ValueError):
                            self.remote_rtp_port = None
                    self.call_id = msg.get("call_id") or self.call_id
                    self._send({"type": "ACK", "call_id": self.call_id, "rtp_port": self.local_rtp_port})
                    self._set_connected()
                else:
                    self.logger.info("Incoming HELLO from %s:%d (tie lost, rejecting)", addr[0], addr[1])
                    self._send({"type": "BUSY"})
                return
            if self.state == "connected":
                if addr == self.remote_addr:
                    if remote_rtp is not None:
                        try:
                            self.remote_rtp_port = int(remote_rtp)
                        except (TypeError, ValueError):
                            self.remote_rtp_port = None
                    self._send({"type": "ACK", "call_id": self.call_id, "rtp_port": self.local_rtp_port})
                    self._mark_seen()
                return
            self.logger.info("Client connected from %s:%d", addr[0], addr[1])
            self.remote_addr = addr
            if remote_rtp is not None:
                try:
                    self.remote_rtp_port = int(remote_rtp)
                except (TypeError, ValueError):
                    self.remote_rtp_port = None
            self.call_id = msg.get("call_id") or str(uuid.uuid4())
            self.tie = int(uuid.uuid4().int & 0x7FFFFFFF)
            self._send({"type": "ACK", "call_id": self.call_id, "rtp_port": self.local_rtp_port})
            self._set_connected()

    def _handle_ack(self, msg, addr):
        with self.lock:
            msg_id = msg.get("call_id") or msg.get("id")
            if self.call_id and msg_id and msg_id != self.call_id:
                return
            if self.state == "calling" and addr == self.remote_addr:
                remote_rtp = msg.get("rtp_port")
                if remote_rtp is not None:
                    try:
                        self.remote_rtp_port = int(remote_rtp)
                    except (TypeError, ValueError):
                        self.remote_rtp_port = None
                self._set_connected()
            elif self.state == "connected" and addr == self.remote_addr:
                self._mark_seen()

    def _handle_keepalive(self, msg, addr):
        with self.lock:
            msg_id = msg.get("call_id") or msg.get("id")
            if self.call_id and msg_id and msg_id != self.call_id:
                return
            if self.remote_addr and addr == self.remote_addr:
                self._mark_seen()

    def _handle_bye(self, msg, addr):
        msg_id = msg.get("call_id") or msg.get("id")
        if self.call_id and msg_id and msg_id != self.call_id:
            return
        if self.remote_addr and addr == self.remote_addr:
            self._set_disconnected("remote bye")

    def _handle_busy(self, addr):
        with self.lock:
            if self.state == "calling":
                self.logger.info("Remote is busy")
                self._set_disconnected("remote busy")

    def _keepalive_loop(self):
        hello_retries = 0
        max_hello_retries = 5
        while self.running:
            time.sleep(self.keepalive_interval)
            with self.lock:
                if self.state == "calling":
                    hello_retries += 1
                    if hello_retries <= max_hello_retries:
                        self.logger.info("Retrying HELLO (%d/%d)", hello_retries, max_hello_retries)
                        self._send({"type": "HELLO", "call_id": self.call_id, "tie": self.tie, "rtp_port": self.local_rtp_port})
                    else:
                        self._set_disconnected("no response")
                        hello_retries = 0
                elif self.state == "connected":
                    hello_retries = 0
                    self._send({"type": "KEEPALIVE"})
                    if self.last_seen:
                        now = time.monotonic()
                        elapsed = now - self.last_seen
                        if elapsed > self.keepalive_timeout:
                            self.keepalive_misses += 1
                        else:
                            self.keepalive_misses = 0
                        if self.keepalive_misses >= self.keepalive_max_misses:
                            self._set_disconnected("keepalive timeout")
                else:
                    hello_retries = 0

    def _accept_message(self, msg, addr):
        if self.allowlist and addr[0] not in self.allowlist:
            return False
        if self.token:
            if msg.get("token") != self.token:
                self.logger.warning("Signaling token mismatch from %s:%d", addr[0], addr[1])
                return False
        return True

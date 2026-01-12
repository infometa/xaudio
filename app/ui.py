import logging
import socket

from PySide6 import QtCore, QtWidgets


class MainWindow(QtWidgets.QMainWindow):
    # Qt signals for thread-safe UI updates
    connected_signal = QtCore.Signal(tuple)
    disconnected_signal = QtCore.Signal()
    
    def __init__(self, media, signaling, metrics, initial_port=5004, auto_listen=False, auto_call=None):
        super().__init__()
        self.media = media
        self.signaling = signaling
        self.metrics = metrics
        self.logger = logging.getLogger("UI")
        self.is_listening = False
        self._initial_port = initial_port
        self._auto_listen = auto_listen
        self._auto_call = auto_call
        self._setup_ui()
        self._connect_signals()
        self._refresh_devices()
        self._start_timer()
        self._apply_initial_settings()

    def _setup_ui(self):
        self.setWindowTitle("TChat P2P Voice")
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        device_group = QtWidgets.QGroupBox("Audio Devices")
        device_layout = QtWidgets.QFormLayout()
        self.input_combo = QtWidgets.QComboBox()
        self.output_combo = QtWidgets.QComboBox()
        self.input_combo.setMinimumWidth(300)
        self.output_combo.setMinimumWidth(300)
        device_layout.addRow("Input (Microphone)", self.input_combo)
        device_layout.addRow("Output (Speaker)", self.output_combo)
        device_group.setLayout(device_layout)

        conn_group = QtWidgets.QGroupBox("Connection Settings")
        conn_layout = QtWidgets.QFormLayout()
        self.local_port = QtWidgets.QLineEdit("5004")
        self.remote_ip = QtWidgets.QLineEdit("127.0.0.1")
        self.remote_port = QtWidgets.QLineEdit("5004")
        
        self.local_port.setPlaceholderText("e.g., 5004, 5006, 5008...")
        self.remote_ip.setPlaceholderText("e.g., 127.0.0.1 or 192.168.1.100")
        self.remote_port.setPlaceholderText("Remote RTP port")
        
        conn_layout.addRow("Local RTP Port", self.local_port)
        conn_layout.addRow("Remote IP", self.remote_ip)
        conn_layout.addRow("Remote RTP Port", self.remote_port)
        
        tip_label = QtWidgets.QLabel("Tip: Use different Local Ports for multiple instances (5004, 5006, 5008...)")
        tip_label.setStyleSheet("color: #666; font-size: 11px; font-style: italic;")
        tip_label.setWordWrap(True)
        conn_layout.addRow("", tip_label)
        
        conn_group.setLayout(conn_layout)

        self.listen_button = QtWidgets.QPushButton("Start Listen")
        self.call_button = QtWidgets.QPushButton("Call")
        self.hangup_button = QtWidgets.QPushButton("Hangup")
        
        self.listen_button.setMinimumHeight(40)
        self.call_button.setMinimumHeight(40)
        self.hangup_button.setMinimumHeight(40)
        
        self.listen_button.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
        self.call_button.setStyleSheet("QPushButton { background-color: #2196F3; color: white; font-weight: bold; }")
        self.hangup_button.setStyleSheet("QPushButton { background-color: #f44336; color: white; font-weight: bold; }")
        
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.listen_button)
        button_layout.addWidget(self.call_button)
        button_layout.addWidget(self.hangup_button)

        status_group = QtWidgets.QGroupBox("Call Status")
        status_layout = QtWidgets.QFormLayout()
        self.status_label = QtWidgets.QLabel("Idle")
        self.speaking_label = QtWidgets.QLabel("No")
        
        self.status_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.speaking_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        
        status_layout.addRow("Connection", self.status_label)
        status_layout.addRow("Speaking (VAD)", self.speaking_label)
        status_group.setLayout(status_layout)

        metrics_group = QtWidgets.QGroupBox("Performance Metrics")
        metrics_layout = QtWidgets.QFormLayout()
        self.dfn_p50 = QtWidgets.QLabel("-")
        self.dfn_p95 = QtWidgets.QLabel("-")
        self.dfn_bypass = QtWidgets.QLabel("0")
        self.queue_depth = QtWidgets.QLabel("-")
        self.jitter_depth = QtWidgets.QLabel("-")
        self.mic_send = QtWidgets.QLabel("-")
        metrics_layout.addRow("DFN P50 (ms)", self.dfn_p50)
        metrics_layout.addRow("DFN P95 (ms)", self.dfn_p95)
        metrics_layout.addRow("DFN Bypass", self.dfn_bypass)
        metrics_layout.addRow("Queue Depth", self.queue_depth)
        metrics_layout.addRow("Jitter Depth", self.jitter_depth)
        metrics_layout.addRow("Mic→Send (ms)", self.mic_send)
        metrics_group.setLayout(metrics_layout)

        layout.addWidget(device_group)
        layout.addWidget(conn_group)
        layout.addLayout(button_layout)
        layout.addWidget(status_group)
        layout.addWidget(metrics_group)
        central.setLayout(layout)
        self.setCentralWidget(central)

    def _connect_signals(self):
        self.listen_button.clicked.connect(self._on_listen)
        self.call_button.clicked.connect(self._on_call)
        self.hangup_button.clicked.connect(self._on_hangup)
        
        # Connect Qt signals to slots for thread-safe UI updates
        self.connected_signal.connect(self._on_connected_slot)
        self.disconnected_signal.connect(self._on_disconnected_slot)
        
        # Set callbacks that emit signals (thread-safe)
        self.signaling.on_connected = self._on_connected_callback
        self.signaling.on_disconnected = self._on_disconnected_callback

    def _refresh_devices(self):
        sources, sinks = self.media.list_devices()
        self.input_combo.clear()
        self.output_combo.clear()
        
        self.input_combo.addItem("System Default", None)
        self.output_combo.addItem("System Default", None)
        
        for dev in sources:
            name = dev["name"]
            if "built-in" in name.lower() or "default" in name.lower():
                name = f"{name}"
            self.input_combo.addItem(name, dev["id"])
        
        for dev in sinks:
            name = dev["name"]
            if "built-in" in name.lower() or "default" in name.lower():
                name = f"{name}"
            self.output_combo.addItem(name, dev["id"])
        
        if sources and len(sources) > 0:
            for i in range(self.input_combo.count()):
                item_text = self.input_combo.itemText(i)
                if "✓" in item_text or "built-in" in item_text.lower():
                    self.input_combo.setCurrentIndex(i)
                    break
        
        if sinks and len(sinks) > 0:
            for i in range(self.output_combo.count()):
                item_text = self.output_combo.itemText(i)
                if "✓" in item_text or "built-in" in item_text.lower():
                    self.output_combo.setCurrentIndex(i)
                    break

    def _start_timer(self):
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update_metrics)
        self.timer.start(500)

    def _update_metrics(self):
        if self.media.pipeline:
            self.media.poll_metrics()
        data = self.metrics.snapshot()
        self.dfn_p50.setText(self._fmt(data.get("dfn_p50_ms")))
        self.dfn_p95.setText(self._fmt(data.get("dfn_p95_ms")))
        self.dfn_bypass.setText(str(data.get("dfn_bypass")))
        self.jitter_depth.setText(self._fmt(data.get("jitter_depth")))
        self.mic_send.setText(self._fmt(data.get("mic_send_latency_ms")))
        queues = ", ".join(f"{k}:{v}" for k, v in data.get("queue_depths", {}).items())
        self.queue_depth.setText(queues or "-")
        
        is_speaking = data.get("vad_speaking")
        if is_speaking:
            self.speaking_label.setText("Yes")
            self.speaking_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 13px;")
        else:
            self.speaking_label.setText("No")
            self.speaking_label.setStyleSheet("color: #999; font-weight: bold; font-size: 13px;")

    def _fmt(self, value):
        if value is None:
            return "-"
        return f"{value:.1f}" if isinstance(value, float) else str(value)

    def _check_port_available(self, port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.bind(('', port))
            return True
        except OSError:
            return False

    def _warn_port_occupied(self, local_port):
        signaling_port = local_port + 1
        warnings = []
        if not self._check_port_available(local_port):
            warnings.append(f"RTP port {local_port}")
        if not self._check_port_available(signaling_port):
            warnings.append(f"Signaling port {signaling_port}")
        if warnings:
            QtWidgets.QMessageBox.warning(
                self,
                "Port Occupied",
                f"{', '.join(warnings)} may be in use.\nConnection may fail."
            )

    def _on_listen(self):
        if hasattr(self, 'is_listening') and self.is_listening:
            self.signaling.stop()
            self.media.stop()
            self.listen_button.setText("Start Listen")
            self.listen_button.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
            self.status_label.setText("Idle")
            self.status_label.setStyleSheet("color: #666; font-weight: bold; font-size: 13px;")
            self.is_listening = False
            return
        
        try:
            local_port = int(self.local_port.text())
            if not (1024 <= local_port <= 65535):
                raise ValueError("Port must be between 1024 and 65535")
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid Port", f"Invalid local port: {e}")
            return
        
        signaling_port = local_port + 1
        self._warn_port_occupied(local_port)
        input_id = self.input_combo.currentData()
        output_id = self.output_combo.currentData()
        self.signaling.start_listen(signaling_port)
        if not self.media.pipeline:
            self.media.start(local_port, None, None, input_id, output_id)
        self.listen_button.setText("Stop Listen")
        self.listen_button.setStyleSheet("QPushButton { background-color: #f44336; color: white; font-weight: bold; }")
        self.status_label.setText("Listening")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 13px;")
        self.is_listening = True

    def _on_call(self):
        remote_ip = self.remote_ip.text().strip()
        if not remote_ip:
            QtWidgets.QMessageBox.warning(self, "Missing IP", "Please enter remote IP")
            return
        
        try:
            remote_port = int(self.remote_port.text())
            local_port = int(self.local_port.text())
            if not (1024 <= remote_port <= 65535) or not (1024 <= local_port <= 65535):
                raise ValueError("Port must be between 1024 and 65535")
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid Port", f"Invalid port number: {e}")
            return
        
        signaling_port = local_port + 1
        self._warn_port_occupied(local_port)
        input_id = self.input_combo.currentData()
        output_id = self.output_combo.currentData()
        if not self.media.pipeline:
            self.media.start(local_port, remote_ip, remote_port, input_id, output_id)
        self.media.set_remote(remote_ip, remote_port)
        self.signaling.start_listen(signaling_port)
        self.signaling.call(remote_ip, remote_port + 1)
        self.status_label.setText("Calling...")
        self.status_label.setStyleSheet("color: #2196F3; font-weight: bold; font-size: 13px;")

    def _on_hangup(self):
        self.signaling.hangup()
        self.media.set_remote("127.0.0.1", 9)
        self.status_label.setText("Idle")
        self.status_label.setStyleSheet("color: #666; font-weight: bold; font-size: 13px;")

    # Thread-safe callbacks that emit signals
    def _on_connected_callback(self, remote_addr):
        """Called from background thread - emits signal for thread-safe UI update"""
        self.connected_signal.emit(remote_addr if remote_addr else ())

    def _on_disconnected_callback(self):
        """Called from background thread - emits signal for thread-safe UI update"""
        self.disconnected_signal.emit()

    # Qt slots that run in the main thread
    @QtCore.Slot(tuple)
    def _on_connected_slot(self, remote_addr):
        if remote_addr:
            rtp_port = max(1, remote_addr[1] - 1)
            self.media.set_remote(remote_addr[0], rtp_port)
            self.status_label.setText(f"Connected {remote_addr[0]}:{rtp_port}")
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 13px;")
        else:
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 13px;")

    @QtCore.Slot()
    def _on_disconnected_slot(self):
        self.status_label.setText("Disconnected")
        self.status_label.setStyleSheet("color: #ff9800; font-weight: bold; font-size: 13px;")

    def closeEvent(self, event):
        print("Closing application, cleaning up resources...")
        try:
            self.timer.stop()
            self.signaling.stop()
            self.media.stop()
        except Exception as e:
            print(f"Error during cleanup: {e}")
        finally:
            event.accept()
            super().closeEvent(event)

    def _apply_initial_settings(self):
        self.local_port.setText(str(self._initial_port))
        
        if self._auto_call:
            try:
                ip, port = self._auto_call.rsplit(":", 1)
                self.remote_ip.setText(ip)
                self.remote_port.setText(port)
                QtCore.QTimer.singleShot(500, self._on_call)
            except ValueError:
                self.logger.error(f"Invalid auto-call format: {self._auto_call}")
        elif self._auto_listen:
            QtCore.QTimer.singleShot(500, self._on_listen)

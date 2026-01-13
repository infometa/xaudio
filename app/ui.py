import logging
import os
import socket

from PySide6 import QtCore, QtWidgets
from PySide6.QtGui import QFont, QFontDatabase


class ElidedLabel(QtWidgets.QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setText(text)

    def setText(self, text):
        self._full_text = text or ""
        self._update_elide()

    def resizeEvent(self, event):
        self._update_elide()
        super().resizeEvent(event)

    def _update_elide(self):
        if not self._full_text:
            super().setText("")
            return
        fm = self.fontMetrics()
        width = max(0, self.width() - 4)
        elided = fm.elidedText(self._full_text, QtCore.Qt.ElideRight, width)
        super().setText(elided)
        self.setToolTip(self._full_text)


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
        self.is_calling = False
        self._initial_port = initial_port
        self._auto_listen = auto_listen
        self._auto_call = auto_call
        self._setup_ui()
        self._connect_signals()
        self._refresh_devices()
        self._start_timer()
        self._apply_initial_settings()

    def _setup_ui(self):
        self.setWindowTitle("TChat 语音通话")
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        device_group = QtWidgets.QGroupBox("音频设备")
        device_layout = QtWidgets.QFormLayout()
        self.input_combo = QtWidgets.QComboBox()
        self.output_combo = QtWidgets.QComboBox()
        self.input_combo.setMinimumWidth(300)
        self.output_combo.setMinimumWidth(300)
        device_layout.addRow("输入（麦克风）", self.input_combo)
        device_layout.addRow("输出（扬声器）", self.output_combo)
        device_group.setLayout(device_layout)

        conn_group = QtWidgets.QGroupBox("连接设置")
        conn_layout = QtWidgets.QFormLayout()
        self.local_port = QtWidgets.QLineEdit("5004")
        self.remote_ip = QtWidgets.QLineEdit("127.0.0.1")
        self.remote_port = QtWidgets.QLineEdit("5004")
        
        self.local_port.setPlaceholderText("例如 5004, 5006, 5008...")
        self.remote_ip.setPlaceholderText("例如 127.0.0.1 或 192.168.1.100")
        self.remote_port.setPlaceholderText("远端 RTP 端口")
        
        conn_layout.addRow("本地 RTP 端口", self.local_port)
        conn_layout.addRow("远端 IP", self.remote_ip)
        conn_layout.addRow("远端 RTP 端口", self.remote_port)
        
        tip_label = QtWidgets.QLabel("提示：多实例请使用不同本地端口（5004, 5006, 5008...）")
        tip_label.setStyleSheet("color: #666; font-size: 11px; font-style: italic;")
        tip_label.setWordWrap(True)
        conn_layout.addRow("", tip_label)
        
        conn_group.setLayout(conn_layout)

        self.listen_button = QtWidgets.QPushButton("开始监听")
        self.call_button = QtWidgets.QPushButton("呼叫")
        self.hangup_button = QtWidgets.QPushButton("挂断")
        self.listen_button.setObjectName("listenButton")
        self.call_button.setObjectName("callButton")
        self.hangup_button.setObjectName("hangupButton")
        
        self.listen_button.setMinimumHeight(40)
        self.call_button.setMinimumHeight(40)
        self.hangup_button.setMinimumHeight(40)
        self.listen_button.setProperty("active", "false")
        
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.listen_button)
        button_layout.addWidget(self.call_button)
        button_layout.addWidget(self.hangup_button)

        status_group = QtWidgets.QGroupBox("通话状态")
        status_layout = QtWidgets.QFormLayout()
        self.status_label = QtWidgets.QLabel("空闲")
        self.speaking_label = QtWidgets.QLabel("否")
        self.status_label.setObjectName("statusLabel")
        self.speaking_label.setObjectName("speakingLabel")
        
        self._set_label_tone(self.status_label, "muted")
        self._set_label_tone(self.speaking_label, "muted")
        
        status_layout.addRow("连接状态", self.status_label)
        status_layout.addRow("说话检测（VAD）", self.speaking_label)
        status_group.setLayout(status_layout)

        processing_group = QtWidgets.QGroupBox("处理参数")
        processing_form = QtWidgets.QFormLayout()
        self.aec_enable = QtWidgets.QCheckBox()
        self.aec_auto = QtWidgets.QCheckBox()
        self.aec_delay = QtWidgets.QSpinBox()
        self.aec_delay.setRange(0, 500)
        self.aec_delay.setSuffix(" 毫秒")
        self.dfn_enable = QtWidgets.QCheckBox()
        self.dfn_mix_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.dfn_mix_slider.setRange(0, 100)
        self.dfn_mix_value = QtWidgets.QLabel("100%")
        self.dfn_mix_value.setMinimumWidth(48)
        self.dfn_mix_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        dfn_mix_wrap = QtWidgets.QHBoxLayout()
        dfn_mix_wrap.setContentsMargins(0, 0, 0, 0)
        dfn_mix_wrap.setSpacing(8)
        dfn_mix_wrap.addWidget(self.dfn_mix_slider)
        dfn_mix_wrap.addWidget(self.dfn_mix_value)
        dfn_mix_widget = QtWidgets.QWidget()
        dfn_mix_widget.setLayout(dfn_mix_wrap)

        self.dfn_post_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.dfn_post_slider.setRange(0, 100)
        self.dfn_post_value = QtWidgets.QLabel("0%")
        self.dfn_post_value.setMinimumWidth(48)
        self.dfn_post_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        dfn_post_wrap = QtWidgets.QHBoxLayout()
        dfn_post_wrap.setContentsMargins(0, 0, 0, 0)
        dfn_post_wrap.setSpacing(8)
        dfn_post_wrap.addWidget(self.dfn_post_slider)
        dfn_post_wrap.addWidget(self.dfn_post_value)
        dfn_post_widget = QtWidgets.QWidget()
        dfn_post_widget.setLayout(dfn_post_wrap)

        self.aec_status = QtWidgets.QLabel("-")
        self.dfn_status = QtWidgets.QLabel("-")
        self.aec_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.dfn_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        processing_form.addRow("AEC 启用", self.aec_enable)
        processing_form.addRow("AEC 自动延迟", self.aec_auto)
        processing_form.addRow("AEC 延迟（毫秒）", self.aec_delay)
        processing_form.addRow("DFN 启用", self.dfn_enable)
        processing_form.addRow("降噪等级（DFN Mix）", dfn_mix_widget)
        processing_form.addRow("后滤波（Post Filter）", dfn_post_widget)
        processing_form.addRow("AEC 状态", self.aec_status)
        processing_form.addRow("DFN 状态", self.dfn_status)
        processing_group.setLayout(processing_form)

        metrics_group = QtWidgets.QGroupBox("性能指标")
        metrics_layout = QtWidgets.QVBoxLayout()
        metrics_layout.setSpacing(6)
        self.metric_titles = {
            "dfn_p50": "降噪 P50（DFN P50, ms）",
            "dfn_p95": "降噪 P95（DFN P95, ms）",
            "dfn_bypass": "降噪旁路（DFN Bypass）",
            "queue_depth": "队列深度（Queue Depth）",
            "jitter_depth": "抖动缓冲（Jitter）",
            "mic_send": "麦克风→发送（Mic→Send, ms）",
            "vad_prob": "VAD 概率（VAD Prob）",
            "vad_energy": "VAD 能量（VAD Energy, dB）",
        }
        self.dfn_p50 = self._make_metric_label(self.metric_titles["dfn_p50"])
        self.dfn_p95 = self._make_metric_label(self.metric_titles["dfn_p95"])
        self.dfn_bypass = self._make_metric_label(self.metric_titles["dfn_bypass"])
        self.queue_depth = self._make_metric_label(self.metric_titles["queue_depth"])
        self.jitter_depth = self._make_metric_label(self.metric_titles["jitter_depth"])
        self.mic_send = self._make_metric_label(self.metric_titles["mic_send"])
        self.vad_prob = self._make_metric_label(self.metric_titles["vad_prob"])
        self.vad_energy = self._make_metric_label(self.metric_titles["vad_energy"])
        metrics_layout.addWidget(self.dfn_p50)
        metrics_layout.addWidget(self.dfn_p95)
        metrics_layout.addWidget(self.dfn_bypass)
        metrics_layout.addWidget(self.queue_depth)
        metrics_layout.addWidget(self.jitter_depth)
        metrics_layout.addWidget(self.mic_send)
        metrics_layout.addWidget(self.vad_prob)
        metrics_layout.addWidget(self.vad_energy)
        metrics_group.setLayout(metrics_layout)

        content_layout = QtWidgets.QHBoxLayout()
        content_layout.setSpacing(12)

        left_col = QtWidgets.QVBoxLayout()
        left_col.setSpacing(10)
        left_col.addWidget(device_group)
        left_col.addWidget(conn_group)
        left_col.addLayout(button_layout)
        left_col.addStretch(1)

        right_col = QtWidgets.QVBoxLayout()
        right_col.setSpacing(10)
        right_col.addWidget(status_group)
        right_col.addWidget(processing_group)
        right_col.addWidget(metrics_group)
        right_col.addStretch(1)

        content_layout.addLayout(left_col, 1)
        content_layout.addLayout(right_col, 1)

        layout.addLayout(content_layout)
        central.setLayout(layout)
        self.setCentralWidget(central)
        self._apply_theme()

    def _connect_signals(self):
        self.listen_button.clicked.connect(self._on_listen)
        self.call_button.clicked.connect(self._on_call)
        self.hangup_button.clicked.connect(self._on_hangup)
        self.aec_enable.toggled.connect(self._on_aec_toggle)
        self.aec_delay.valueChanged.connect(self._on_aec_delay_changed)
        self.aec_auto.toggled.connect(self._on_aec_auto_toggle)
        self.dfn_enable.toggled.connect(self._on_dfn_toggle)
        self.dfn_mix_slider.valueChanged.connect(self._on_dfn_mix_changed)
        self.dfn_post_slider.valueChanged.connect(self._on_dfn_post_changed)
        
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
        
        self.input_combo.addItem("系统默认", None)
        self.output_combo.addItem("系统默认", None)
        
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
        if self.media.dfn_active is False:
            self._set_metric(self.dfn_p50, self.metric_titles["dfn_p50"], "不可用")
            self._set_metric(self.dfn_p95, self.metric_titles["dfn_p95"], "不可用")
            self._set_metric(self.dfn_bypass, self.metric_titles["dfn_bypass"], "-")
        else:
            self._set_metric(self.dfn_p50, self.metric_titles["dfn_p50"], self._fmt(data.get("dfn_p50_ms")))
            self._set_metric(self.dfn_p95, self.metric_titles["dfn_p95"], self._fmt(data.get("dfn_p95_ms")))
            self._set_metric(self.dfn_bypass, self.metric_titles["dfn_bypass"], str(data.get("dfn_bypass")))
        self._set_metric(self.jitter_depth, self.metric_titles["jitter_depth"], self._fmt_jitter(data.get("jitter_depth"), data.get("jitter_kind")))
        self._set_metric(self.mic_send, self.metric_titles["mic_send"], self._fmt(data.get("mic_send_latency_ms")))
        self._set_metric(self.vad_prob, self.metric_titles["vad_prob"], self._fmt(data.get("vad_prob")))
        self._set_metric(self.vad_energy, self.metric_titles["vad_energy"], self._fmt(data.get("vad_energy_db")))
        queues = self._format_queue_depths(data.get("queue_depths", {}))
        self._set_metric(self.queue_depth, self.metric_titles["queue_depth"], queues)
        
        is_speaking = data.get("vad_speaking")
        if is_speaking:
            self.speaking_label.setText("是")
            self._set_label_tone(self.speaking_label, "success")
        else:
            self.speaking_label.setText("否")
            self._set_label_tone(self.speaking_label, "muted")
        self._update_pipeline_flags()

    def _fmt(self, value):
        if value is None:
            return "-"
        return f"{value:.1f}" if isinstance(value, float) else str(value)

    def _set_metric(self, label, title, value):
        label.setText(f"{title}：{value}")

    def _fmt_jitter(self, value, kind):
        if value is None:
            return "-"
        if kind == "queue":
            return f"{int(value)} pkt"
        if kind == "avg-jitter-ms":
            return f"{value:.1f} ms" if isinstance(value, float) else f"{value} ms"
        return self._fmt(value)

    def _format_queue_depths(self, queue_depths):
        if not queue_depths:
            return "-"
        name_map = {
            "capture_q": "采集",
            "vad_q": "VAD",
            "dfn_q": "DFN",
            "post_dfn_q": "后处理",
            "playout_q": "播放",
            "render_q": "渲染",
        }
        items = [f"{name_map.get(k, k)}:{v}" for k, v in queue_depths.items()]
        return "、".join(items)

    def _apply_theme(self):
        font = self._pick_font([
            "PingFang SC",
            "Hiragino Sans GB",
            "Heiti SC",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "Microsoft YaHei",
            "Avenir Next",
            "SF Pro Text",
        ])
        if font:
            self.setFont(font)
        self.setStyleSheet("""
        QMainWindow {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                        stop:0 #F5F7FB, stop:1 #EEF2F8);
        }
        QWidget {
            color: #1F2937;
            font-size: 12px;
        }
        QGroupBox {
            background: #FFFFFF;
            border: 1px solid #E0E7F0;
            border-radius: 14px;
            margin-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: #4B5B71;
            font-weight: 600;
        }
        QLineEdit, QComboBox, QSpinBox {
            background: #F8FAFC;
            border: 1px solid #D8E0EA;
            border-radius: 8px;
            padding: 6px 8px;
        }
        QSlider::groove:horizontal {
            height: 6px;
            background: #E7EDF5;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            width: 16px;
            margin: -5px 0;
            border-radius: 8px;
            background: #2563EB;
        }
        QSlider::sub-page:horizontal {
            background: #93C5FD;
            border-radius: 3px;
        }
        QComboBox::drop-down {
            border: none;
        }
        QComboBox::down-arrow {
            image: none;
        }
        QPushButton {
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }
        QPushButton#listenButton {
            background: #10B981;
            color: white;
        }
        QPushButton#listenButton[active="true"] {
            background: #EF4444;
        }
        QPushButton#callButton {
            background: #2563EB;
            color: white;
        }
        QPushButton#hangupButton {
            background: #EF4444;
            color: white;
        }
        QPushButton:disabled {
            background: #E5E7EB;
            color: #9CA3AF;
        }
        QLabel#statusLabel, QLabel#speakingLabel {
            padding: 4px 10px;
            border-radius: 999px;
            background: #EEF2F7;
            color: #1F2937;
            font-weight: 600;
        }
        QLabel#statusLabel[tone="success"], QLabel#speakingLabel[tone="success"] {
            background: #E7F8EF;
            color: #147D45;
        }
        QLabel#statusLabel[tone="info"], QLabel#speakingLabel[tone="info"] {
            background: #E8F1FF;
            color: #1E4BB8;
        }
        QLabel#statusLabel[tone="warn"], QLabel#speakingLabel[tone="warn"] {
            background: #FFF4E6;
            color: #B45309;
        }
        QLabel#statusLabel[tone="muted"], QLabel#speakingLabel[tone="muted"] {
            background: #EEF2F7;
            color: #6B7280;
        }
        """)

    def _pick_font(self, families, size=12):
        db = QFontDatabase()
        available = set(db.families())
        for family in families:
            if family in available:
                return QFont(family, size)
        return None

    def _update_pipeline_flags(self):
        aec_text = self._format_flag(self.media.disable_aec_env, self.media.aec_active, self.media.aec_enabled)
        dfn_text = self._format_flag(self.media.disable_dfn_env, self.media.dfn_active, self.media.dfn_enabled)
        self.aec_status.setText(aec_text)
        self.dfn_status.setText(dfn_text)
        self._update_processing_controls()

    def _format_flag(self, disabled_env, active, enabled):
        if disabled_env:
            return "已禁用（环境）"
        if active is None:
            return "待初始化"
        if not active:
            return "不可用"
        if not enabled:
            return "已旁路"
        return "已启用"

    def _update_processing_controls(self):
        aec_available = self.media.aec_active is not False
        dfn_available = self.media.dfn_active is not False
        aec_enabled = self.media.aec_enabled
        dfn_enabled = self.media.dfn_enabled

        self._set_checkbox_silent(self.aec_enable, aec_enabled)
        self._set_checkbox_silent(self.aec_auto, self.media.aec_auto_delay)
        self._set_checkbox_silent(self.dfn_enable, dfn_enabled)
        self._set_spin_silent(self.aec_delay, int(self.media.aec_delay_ms))
        self._set_slider_silent(self.dfn_mix_slider, int(self.media.dfn_mix * 100))
        self._set_slider_silent(self.dfn_post_slider, int(self.media.dfn_post_filter * 100))
        self.dfn_mix_value.setText(f"{int(self.media.dfn_mix * 100)}%")
        self.dfn_post_value.setText(f"{int(self.media.dfn_post_filter * 100)}%")

        aec_controls_enabled = not self.media.disable_aec_env and aec_available
        dfn_controls_enabled = not self.media.disable_dfn_env and dfn_available
        self.aec_enable.setEnabled(aec_controls_enabled)
        self.aec_auto.setEnabled(aec_controls_enabled)
        self.aec_delay.setEnabled(aec_controls_enabled and not self.media.aec_auto_delay)
        self.dfn_enable.setEnabled(dfn_controls_enabled)
        self.dfn_mix_slider.setEnabled(dfn_controls_enabled)
        self.dfn_post_slider.setEnabled(dfn_controls_enabled)

    def _set_checkbox_silent(self, checkbox, value):
        prev = checkbox.blockSignals(True)
        checkbox.setChecked(bool(value))
        checkbox.blockSignals(prev)

    def _set_spin_silent(self, spin, value):
        prev = spin.blockSignals(True)
        spin.setValue(int(value))
        spin.blockSignals(prev)

    def _set_slider_silent(self, slider, value):
        prev = slider.blockSignals(True)
        slider.setValue(int(value))
        slider.blockSignals(prev)

    def _make_metric_label(self, title):
        label = ElidedLabel(f"{title}：-")
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        label.setWordWrap(False)
        label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        return label

    def _on_aec_toggle(self, checked):
        if self.media.disable_aec_env:
            return
        self.media.set_processing_options(aec_enabled=checked)
        self._update_pipeline_flags()

    def _on_aec_delay_changed(self, value):
        if self.media.aec_auto_delay:
            return
        self.media.set_processing_options(aec_delay_ms=value)

    def _on_aec_auto_toggle(self, checked):
        self.media.set_processing_options(aec_auto_delay=checked)
        self._update_pipeline_flags()

    def _on_dfn_toggle(self, checked):
        if self.media.disable_dfn_env:
            return
        self.media.set_processing_options(dfn_enabled=checked)
        self._update_pipeline_flags()

    def _on_dfn_mix_changed(self, value):
        self.dfn_mix_value.setText(f"{value}%")
        self.media.set_processing_options(dfn_mix=value / 100.0)

    def _on_dfn_post_changed(self, value):
        self.dfn_post_value.setText(f"{value}%")
        self.media.set_processing_options(dfn_post_filter=value / 100.0)

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
            warnings.append(f"RTP 端口 {local_port}")
        if not self._check_port_available(signaling_port):
            warnings.append(f"信令端口 {signaling_port}")
        if warnings:
            QtWidgets.QMessageBox.warning(
                self,
                "端口被占用",
                f"{'，'.join(warnings)} 可能已被占用。\n连接可能失败。"
            )

    def _set_label_tone(self, label, tone):
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def _set_button_active(self, button, active):
        button.setProperty("active", "true" if active else "false")
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def _on_listen(self):
        if hasattr(self, 'is_listening') and self.is_listening:
            self.signaling.stop()
            self.media.stop()
            self.listen_button.setText("开始监听")
            self._set_button_active(self.listen_button, False)
            self.status_label.setText("空闲")
            self._set_label_tone(self.status_label, "muted")
            self.speaking_label.setText("否")
            self._set_label_tone(self.speaking_label, "muted")
            self.is_listening = False
            return
        
        try:
            local_port = int(self.local_port.text())
            if not (1024 <= local_port <= 65535):
                raise ValueError("端口必须在 1024~65535 之间")
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "端口无效", f"本地端口无效：{e}")
            return
        
        signaling_port = local_port + 1
        self._warn_port_occupied(local_port)
        input_id = self.input_combo.currentData()
        output_id = self.output_combo.currentData()
        self.signaling.start_listen(signaling_port)
        if not self.media.pipeline:
            try:
                self.media.start(local_port, None, None, input_id, output_id)
            except Exception as exc:
                self.signaling.stop()
                QtWidgets.QMessageBox.critical(self, "启动失败", f"音频管线启动失败：{exc}")
                return
        self.listen_button.setText("停止监听")
        self._set_button_active(self.listen_button, True)
        self.status_label.setText("监听中")
        self._set_label_tone(self.status_label, "info")
        self.is_listening = True

    def _on_call(self):
        remote_ip = self.remote_ip.text().strip()
        if not remote_ip:
            QtWidgets.QMessageBox.warning(self, "缺少 IP", "请输入远端 IP")
            return
        
        try:
            remote_port = int(self.remote_port.text())
            local_port = int(self.local_port.text())
            if not (1024 <= remote_port <= 65535) or not (1024 <= local_port <= 65535):
                raise ValueError("端口必须在 1024~65535 之间")
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "端口无效", f"端口无效：{e}")
            return
        
        signaling_port = local_port + 1
        self._warn_port_occupied(local_port)
        input_id = self.input_combo.currentData()
        output_id = self.output_combo.currentData()
        if not self.media.pipeline:
            try:
                self.media.start(local_port, remote_ip, remote_port, input_id, output_id)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "启动失败", f"音频管线启动失败：{exc}")
                return
        self.media.set_remote(remote_ip, remote_port)
        self.media.set_send_enabled(True)
        self.signaling.start_listen(signaling_port)
        self.signaling.call(remote_ip, remote_port + 1)
        self.status_label.setText("呼叫中...")
        self._set_label_tone(self.status_label, "info")
        self.is_calling = True
        self.call_button.setEnabled(False)

    def _on_hangup(self):
        self.signaling.hangup()
        self.media.set_send_enabled(False)
        self.status_label.setText("空闲")
        self._set_label_tone(self.status_label, "muted")
        self.speaking_label.setText("否")
        self._set_label_tone(self.speaking_label, "muted")
        self.is_calling = False
        self.call_button.setEnabled(True)

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
            if self.media.pipeline and self.media.is_listen_only:
                self.media.restart_with_remote(remote_addr[0], rtp_port)
            else:
                self.media.set_remote(remote_addr[0], rtp_port)
            self.media.set_send_enabled(True)
            self.status_label.setText(f"已连接 {remote_addr[0]}:{rtp_port}")
            self._set_label_tone(self.status_label, "success")
        else:
            self.status_label.setText("已连接")
            self._set_label_tone(self.status_label, "success")
        self.is_calling = True
        self.call_button.setEnabled(False)

    @QtCore.Slot()
    def _on_disconnected_slot(self):
        self.media.set_send_enabled(False)
        self.status_label.setText("已断开")
        self._set_label_tone(self.status_label, "warn")
        self.speaking_label.setText("否")
        self._set_label_tone(self.speaking_label, "muted")
        self.is_calling = False
        self.call_button.setEnabled(True)

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
        default_remote_ip = os.getenv("TCHAT_DEFAULT_REMOTE_IP")
        default_remote_port = os.getenv("TCHAT_DEFAULT_REMOTE_PORT")
        if default_remote_ip:
            self.remote_ip.setText(default_remote_ip)
        if default_remote_port:
            self.remote_port.setText(default_remote_port)
        self._update_processing_controls()

        if self._auto_call:
            try:
                ip, port = self._auto_call.rsplit(":", 1)
                self.remote_ip.setText(ip)
                self.remote_port.setText(port)
            except ValueError:
                self.logger.error(f"Invalid auto-call format: {self._auto_call}")

        if self._auto_listen or self._auto_call:
            self.logger.info("Auto listen/call is disabled; select devices and start manually.")

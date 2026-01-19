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
    media_error_signal = QtCore.Signal(str)
    media_warning_signal = QtCore.Signal(str)
    
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
        self.resize(1060, 740)
        self.setMinimumSize(920, 640)
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

        pipeline_card = QtWidgets.QFrame()
        pipeline_card.setObjectName("pipelineCard")
        pipeline_layout = QtWidgets.QVBoxLayout(pipeline_card)
        pipeline_layout.setContentsMargins(12, 10, 12, 10)
        pipeline_layout.setSpacing(6)
        pipeline_title = QtWidgets.QLabel("当前音频处理流程")
        pipeline_title.setObjectName("pipelineTitle")
        self.pipeline_main = QtWidgets.QLabel()
        self.pipeline_main.setWordWrap(True)
        self.pipeline_main.setObjectName("pipelineFlow")
        self.pipeline_vad = QtWidgets.QLabel()
        self.pipeline_vad.setWordWrap(True)
        self.pipeline_vad.setObjectName("pipelineFlow")
        self.pipeline_downlink = QtWidgets.QLabel()
        self.pipeline_downlink.setWordWrap(True)
        self.pipeline_downlink.setObjectName("pipelineFlow")
        pipeline_layout.addWidget(pipeline_title)
        pipeline_layout.addWidget(self.pipeline_main)
        pipeline_layout.addWidget(self.pipeline_vad)
        pipeline_layout.addWidget(self.pipeline_downlink)

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

        self.dfn_vad_link_main = QtWidgets.QCheckBox()

        self.aec_status = QtWidgets.QLabel("-")
        self.dfn_status = QtWidgets.QLabel("-")
        self.hpf_status = QtWidgets.QLabel("-")
        self.eq_status = QtWidgets.QLabel("-")
        self.cng_status = QtWidgets.QLabel("-")
        self.limiter_status = QtWidgets.QLabel("-")
        self.aec_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.dfn_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.hpf_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.eq_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.cng_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.limiter_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        processing_form.addRow("AEC 启用", self.aec_enable)
        processing_form.addRow("AEC 自动延迟", self.aec_auto)
        processing_form.addRow("AEC 延迟（毫秒）", self.aec_delay)
        processing_form.addRow("DFN 启用", self.dfn_enable)
        processing_form.addRow("降噪等级（DFN Mix）", dfn_mix_widget)
        processing_form.addRow("后滤波（Post Filter）", dfn_post_widget)
        processing_form.addRow("VAD 联动降噪", self.dfn_vad_link_main)
        processing_form.addRow("AEC 状态", self.aec_status)
        processing_form.addRow("DFN 状态", self.dfn_status)
        processing_form.addRow("HPF 状态", self.hpf_status)
        processing_form.addRow("EQ 状态", self.eq_status)
        processing_form.addRow("CNG 状态", self.cng_status)
        processing_form.addRow("Limiter 状态", self.limiter_status)
        processing_group.setLayout(processing_form)

        self.hpf_enable = QtWidgets.QCheckBox()
        self.hpf_cutoff_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.hpf_cutoff_slider.setRange(60, 200)
        self.hpf_cutoff_value = QtWidgets.QLabel("120 Hz")
        self.hpf_cutoff_value.setMinimumWidth(60)
        self.hpf_cutoff_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        hpf_cutoff_wrap = QtWidgets.QHBoxLayout()
        hpf_cutoff_wrap.setContentsMargins(0, 0, 0, 0)
        hpf_cutoff_wrap.setSpacing(8)
        hpf_cutoff_wrap.addWidget(self.hpf_cutoff_slider)
        hpf_cutoff_wrap.addWidget(self.hpf_cutoff_value)
        hpf_cutoff_widget = QtWidgets.QWidget()
        hpf_cutoff_widget.setLayout(hpf_cutoff_wrap)

        self.agc_enable = QtWidgets.QCheckBox()
        self.agc_input = QtWidgets.QCheckBox()
        self.agc_headroom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.agc_headroom_slider.setRange(0, 12)
        self.agc_headroom_value = QtWidgets.QLabel("5 dB")
        self.agc_headroom_value.setMinimumWidth(60)
        self.agc_headroom_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        agc_headroom_wrap = QtWidgets.QHBoxLayout()
        agc_headroom_wrap.setContentsMargins(0, 0, 0, 0)
        agc_headroom_wrap.setSpacing(8)
        agc_headroom_wrap.addWidget(self.agc_headroom_slider)
        agc_headroom_wrap.addWidget(self.agc_headroom_value)
        agc_headroom_widget = QtWidgets.QWidget()
        agc_headroom_widget.setLayout(agc_headroom_wrap)

        self.agc_max_gain_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.agc_max_gain_slider.setRange(0, 60)
        self.agc_max_gain_value = QtWidgets.QLabel("50 dB")
        self.agc_max_gain_value.setMinimumWidth(60)
        self.agc_max_gain_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        agc_max_gain_wrap = QtWidgets.QHBoxLayout()
        agc_max_gain_wrap.setContentsMargins(0, 0, 0, 0)
        agc_max_gain_wrap.setSpacing(8)
        agc_max_gain_wrap.addWidget(self.agc_max_gain_slider)
        agc_max_gain_wrap.addWidget(self.agc_max_gain_value)
        agc_max_gain_widget = QtWidgets.QWidget()
        agc_max_gain_widget.setLayout(agc_max_gain_wrap)

        self.agc_initial_gain_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.agc_initial_gain_slider.setRange(0, 30)
        self.agc_initial_gain_value = QtWidgets.QLabel("15 dB")
        self.agc_initial_gain_value.setMinimumWidth(60)
        self.agc_initial_gain_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        agc_initial_gain_wrap = QtWidgets.QHBoxLayout()
        agc_initial_gain_wrap.setContentsMargins(0, 0, 0, 0)
        agc_initial_gain_wrap.setSpacing(8)
        agc_initial_gain_wrap.addWidget(self.agc_initial_gain_slider)
        agc_initial_gain_wrap.addWidget(self.agc_initial_gain_value)
        agc_initial_gain_widget = QtWidgets.QWidget()
        agc_initial_gain_widget.setLayout(agc_initial_gain_wrap)

        self.agc_noise_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.agc_noise_slider.setRange(-80, -30)
        self.agc_noise_value = QtWidgets.QLabel("-50 dBFS")
        self.agc_noise_value.setMinimumWidth(70)
        self.agc_noise_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        agc_noise_wrap = QtWidgets.QHBoxLayout()
        agc_noise_wrap.setContentsMargins(0, 0, 0, 0)
        agc_noise_wrap.setSpacing(8)
        agc_noise_wrap.addWidget(self.agc_noise_slider)
        agc_noise_wrap.addWidget(self.agc_noise_value)
        agc_noise_widget = QtWidgets.QWidget()
        agc_noise_widget.setLayout(agc_noise_wrap)

        self.eq_enable = QtWidgets.QCheckBox()
        self.eq_low_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.eq_low_slider.setRange(-12, 12)
        self.eq_low_value = QtWidgets.QLabel("0 dB")
        self.eq_low_value.setMinimumWidth(50)
        self.eq_low_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        eq_low_wrap = QtWidgets.QHBoxLayout()
        eq_low_wrap.setContentsMargins(0, 0, 0, 0)
        eq_low_wrap.setSpacing(8)
        eq_low_wrap.addWidget(self.eq_low_slider)
        eq_low_wrap.addWidget(self.eq_low_value)
        eq_low_widget = QtWidgets.QWidget()
        eq_low_widget.setLayout(eq_low_wrap)

        self.eq_mid_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.eq_mid_slider.setRange(-12, 12)
        self.eq_mid_value = QtWidgets.QLabel("0 dB")
        self.eq_mid_value.setMinimumWidth(50)
        self.eq_mid_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        eq_mid_wrap = QtWidgets.QHBoxLayout()
        eq_mid_wrap.setContentsMargins(0, 0, 0, 0)
        eq_mid_wrap.setSpacing(8)
        eq_mid_wrap.addWidget(self.eq_mid_slider)
        eq_mid_wrap.addWidget(self.eq_mid_value)
        eq_mid_widget = QtWidgets.QWidget()
        eq_mid_widget.setLayout(eq_mid_wrap)

        self.eq_high_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.eq_high_slider.setRange(-12, 12)
        self.eq_high_value = QtWidgets.QLabel("0 dB")
        self.eq_high_value.setMinimumWidth(50)
        self.eq_high_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        eq_high_wrap = QtWidgets.QHBoxLayout()
        eq_high_wrap.setContentsMargins(0, 0, 0, 0)
        eq_high_wrap.setSpacing(8)
        eq_high_wrap.addWidget(self.eq_high_slider)
        eq_high_wrap.addWidget(self.eq_high_value)
        eq_high_widget = QtWidgets.QWidget()
        eq_high_widget.setLayout(eq_high_wrap)

        self.cng_enable = QtWidgets.QCheckBox()
        self.cng_level_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.cng_level_slider.setRange(-80, -50)
        self.cng_level_value = QtWidgets.QLabel("-65 dB")
        self.cng_level_value.setMinimumWidth(60)
        self.cng_level_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        cng_level_wrap = QtWidgets.QHBoxLayout()
        cng_level_wrap.setContentsMargins(0, 0, 0, 0)
        cng_level_wrap.setSpacing(8)
        cng_level_wrap.addWidget(self.cng_level_slider)
        cng_level_wrap.addWidget(self.cng_level_value)
        cng_level_widget = QtWidgets.QWidget()
        cng_level_widget.setLayout(cng_level_wrap)

        self.dfn_vad_link = QtWidgets.QCheckBox()
        self.dfn_mix_speech_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.dfn_mix_speech_slider.setRange(0, 100)
        self.dfn_mix_speech_value = QtWidgets.QLabel("85%")
        self.dfn_mix_speech_value.setMinimumWidth(50)
        self.dfn_mix_speech_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        dfn_mix_speech_wrap = QtWidgets.QHBoxLayout()
        dfn_mix_speech_wrap.setContentsMargins(0, 0, 0, 0)
        dfn_mix_speech_wrap.setSpacing(8)
        dfn_mix_speech_wrap.addWidget(self.dfn_mix_speech_slider)
        dfn_mix_speech_wrap.addWidget(self.dfn_mix_speech_value)
        dfn_mix_speech_widget = QtWidgets.QWidget()
        dfn_mix_speech_widget.setLayout(dfn_mix_speech_wrap)

        self.dfn_mix_silence_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.dfn_mix_silence_slider.setRange(0, 100)
        self.dfn_mix_silence_value = QtWidgets.QLabel("100%")
        self.dfn_mix_silence_value.setMinimumWidth(50)
        self.dfn_mix_silence_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        dfn_mix_silence_wrap = QtWidgets.QHBoxLayout()
        dfn_mix_silence_wrap.setContentsMargins(0, 0, 0, 0)
        dfn_mix_silence_wrap.setSpacing(8)
        dfn_mix_silence_wrap.addWidget(self.dfn_mix_silence_slider)
        dfn_mix_silence_wrap.addWidget(self.dfn_mix_silence_value)
        dfn_mix_silence_widget = QtWidgets.QWidget()
        dfn_mix_silence_widget.setLayout(dfn_mix_silence_wrap)

        self.opus_bitrate = QtWidgets.QSpinBox()
        self.opus_bitrate.setRange(16000, 64000)
        self.opus_bitrate.setSuffix(" bps")
        self.opus_fec = QtWidgets.QCheckBox()
        self.opus_dtx = QtWidgets.QCheckBox()
        self.opus_loss = QtWidgets.QSpinBox()
        self.opus_loss.setRange(0, 20)
        self.opus_loss.setSuffix(" %")

        self.limiter_threshold_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.limiter_threshold_slider.setRange(-20, 0)
        self.limiter_threshold_value = QtWidgets.QLabel("-1 dB")
        self.limiter_threshold_value.setMinimumWidth(60)
        self.limiter_threshold_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        limiter_threshold_wrap = QtWidgets.QHBoxLayout()
        limiter_threshold_wrap.setContentsMargins(0, 0, 0, 0)
        limiter_threshold_wrap.setSpacing(8)
        limiter_threshold_wrap.addWidget(self.limiter_threshold_slider)
        limiter_threshold_wrap.addWidget(self.limiter_threshold_value)
        limiter_threshold_widget = QtWidgets.QWidget()
        limiter_threshold_widget.setLayout(limiter_threshold_wrap)

        self.limiter_attack_spin = QtWidgets.QSpinBox()
        self.limiter_attack_spin.setRange(1, 50)
        self.limiter_attack_spin.setSuffix(" ms")
        self.limiter_release_spin = QtWidgets.QSpinBox()
        self.limiter_release_spin.setRange(10, 200)
        self.limiter_release_spin.setSuffix(" ms")

        advanced_subtabs = QtWidgets.QTabWidget()
        advanced_subtabs.setObjectName("advancedSubTabs")
        advanced_subtabs.setUsesScrollButtons(True)
        advanced_subtabs.setElideMode(QtCore.Qt.ElideNone)

        hpf_agc_page = QtWidgets.QWidget()
        hpf_agc_form = QtWidgets.QFormLayout()
        hpf_agc_form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        hpf_agc_form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        hpf_agc_form.addRow("高通滤波（HPF）", self.hpf_enable)
        hpf_agc_form.addRow("HPF 截止频率", hpf_cutoff_widget)
        hpf_agc_form.addRow("AGC 启用", self.agc_enable)
        hpf_agc_form.addRow("AGC 输入音量控制", self.agc_input)
        hpf_agc_form.addRow("AGC 余量（Headroom）", agc_headroom_widget)
        hpf_agc_form.addRow("AGC 最大增益（Max Gain）", agc_max_gain_widget)
        hpf_agc_form.addRow("AGC 初始增益（Initial）", agc_initial_gain_widget)
        hpf_agc_form.addRow("AGC 噪声上限（Noise）", agc_noise_widget)
        hpf_agc_page.setLayout(hpf_agc_form)

        eq_page = QtWidgets.QWidget()
        eq_form = QtWidgets.QFormLayout()
        eq_form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        eq_form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        eq_form.addRow("均衡器（EQ）", self.eq_enable)
        eq_form.addRow("EQ 低频", eq_low_widget)
        eq_form.addRow("EQ 中频", eq_mid_widget)
        eq_form.addRow("EQ 高频", eq_high_widget)
        eq_page.setLayout(eq_form)

        noise_page = QtWidgets.QWidget()
        noise_form = QtWidgets.QFormLayout()
        noise_form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        noise_form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        noise_form.addRow("舒适噪声（CNG）", self.cng_enable)
        noise_form.addRow("CNG 电平", cng_level_widget)
        noise_form.addRow("VAD 联动降噪", self.dfn_vad_link)
        noise_form.addRow("语音时降噪", dfn_mix_speech_widget)
        noise_form.addRow("静默时降噪", dfn_mix_silence_widget)
        noise_page.setLayout(noise_form)

        opus_page = QtWidgets.QWidget()
        opus_form = QtWidgets.QFormLayout()
        opus_form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        opus_form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        opus_form.addRow("Opus 比特率", self.opus_bitrate)
        opus_form.addRow("Opus 前向纠错（FEC）", self.opus_fec)
        opus_form.addRow("Opus 静音检测（DTX）", self.opus_dtx)
        opus_form.addRow("Opus 预期丢包", self.opus_loss)
        opus_page.setLayout(opus_form)

        limiter_page = QtWidgets.QWidget()
        limiter_form = QtWidgets.QFormLayout()
        limiter_form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        limiter_form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        limiter_form.addRow("Limiter 阈值", limiter_threshold_widget)
        limiter_form.addRow("Limiter Attack", self.limiter_attack_spin)
        limiter_form.addRow("Limiter Release", self.limiter_release_spin)
        limiter_page.setLayout(limiter_form)

        advanced_subtabs.addTab(hpf_agc_page, "HPF/AGC")
        advanced_subtabs.addTab(eq_page, "EQ")
        advanced_subtabs.addTab(noise_page, "CNG/DFN")
        advanced_subtabs.addTab(opus_page, "Opus")
        advanced_subtabs.addTab(limiter_page, "Limiter")

        metrics_group = QtWidgets.QGroupBox("性能指标")
        metrics_layout = QtWidgets.QVBoxLayout()
        metrics_layout.setSpacing(6)
        self.metric_titles = {
            "dfn_p50": "降噪 P50（DFN P50, ms）",
            "dfn_p95": "降噪 P95（DFN P95, ms）",
            "dfn_bypass": "降噪旁路（DFN Bypass）",
            "dfn_auto_mix": "降噪自动混合（DFN Auto Mix）",
            "aec_erle": "回声消除 ERLE（ERLE, dB）",
            "aec_erl": "回声消除 ERL（ERL, dB）",
            "aec_delay": "AEC 延迟估计（ms）",
            "queue_depth": "队列深度（Queue Depth）",
            "jitter_depth": "抖动缓冲（Jitter）",
            "mic_send": "麦克风→发送（Mic→Send, ms）",
            "vad_prob": "VAD 概率（VAD Prob）",
            "vad_energy": "VAD 能量（VAD Energy, dB）",
            "sample_rate": "采样率（输入/目标, Hz）",
        }
        self.dfn_p50 = self._make_metric_label(self.metric_titles["dfn_p50"])
        self.dfn_p95 = self._make_metric_label(self.metric_titles["dfn_p95"])
        self.dfn_bypass = self._make_metric_label(self.metric_titles["dfn_bypass"])
        self.dfn_auto_mix = self._make_metric_label(self.metric_titles["dfn_auto_mix"])
        self.aec_erle = self._make_metric_label(self.metric_titles["aec_erle"])
        self.aec_erl = self._make_metric_label(self.metric_titles["aec_erl"])
        self.aec_delay_metric = self._make_metric_label(self.metric_titles["aec_delay"])
        self.queue_depth = self._make_metric_label(self.metric_titles["queue_depth"])
        self.jitter_depth = self._make_metric_label(self.metric_titles["jitter_depth"])
        self.mic_send = self._make_metric_label(self.metric_titles["mic_send"])
        self.vad_prob = self._make_metric_label(self.metric_titles["vad_prob"])
        self.vad_energy = self._make_metric_label(self.metric_titles["vad_energy"])
        self.sample_rate = self._make_metric_label(self.metric_titles["sample_rate"])
        metrics_layout.addWidget(self.dfn_p50)
        metrics_layout.addWidget(self.dfn_p95)
        metrics_layout.addWidget(self.dfn_bypass)
        metrics_layout.addWidget(self.dfn_auto_mix)
        metrics_layout.addWidget(self.aec_erle)
        metrics_layout.addWidget(self.aec_erl)
        metrics_layout.addWidget(self.aec_delay_metric)
        metrics_layout.addWidget(self.queue_depth)
        metrics_layout.addWidget(self.jitter_depth)
        metrics_layout.addWidget(self.mic_send)
        metrics_layout.addWidget(self.vad_prob)
        metrics_layout.addWidget(self.vad_energy)
        metrics_layout.addWidget(self.sample_rate)
        metrics_group.setLayout(metrics_layout)

        left_col = QtWidgets.QVBoxLayout()
        left_col.setSpacing(10)
        left_col.addWidget(device_group)
        left_col.addWidget(conn_group)
        left_col.addLayout(button_layout)
        left_col.addWidget(pipeline_card)
        left_col.addStretch(1)

        status_tab = QtWidgets.QWidget()
        status_layout_tab = QtWidgets.QVBoxLayout()
        status_layout_tab.setSpacing(10)
        status_layout_tab.addWidget(status_group)
        status_layout_tab.addWidget(metrics_group)
        status_layout_tab.addStretch(1)
        status_tab.setLayout(status_layout_tab)

        processing_tab = QtWidgets.QWidget()
        processing_layout_tab = QtWidgets.QVBoxLayout()
        processing_layout_tab.setSpacing(10)
        processing_layout_tab.addWidget(processing_group)
        processing_layout_tab.addStretch(1)
        processing_tab.setLayout(processing_layout_tab)

        advanced_tab = QtWidgets.QWidget()
        advanced_layout_tab = QtWidgets.QVBoxLayout()
        advanced_layout_tab.setSpacing(10)
        advanced_layout_tab.addWidget(advanced_subtabs)
        advanced_layout_tab.addStretch(1)
        advanced_tab.setLayout(advanced_layout_tab)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(status_tab, "状态与指标")
        tabs.addTab(processing_tab, "处理参数")
        tabs.addTab(advanced_tab, "高级调参")

        content_layout = QtWidgets.QHBoxLayout()
        content_layout.setSpacing(12)

        content_layout.addLayout(left_col, 1)
        content_layout.addWidget(tabs, 2)

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
        self.agc_enable.toggled.connect(self._on_agc_toggle)
        self.agc_input.toggled.connect(self._on_agc_input_toggle)
        self.agc_headroom_slider.valueChanged.connect(self._on_agc_headroom_changed)
        self.agc_max_gain_slider.valueChanged.connect(self._on_agc_max_gain_changed)
        self.agc_initial_gain_slider.valueChanged.connect(self._on_agc_initial_gain_changed)
        self.agc_noise_slider.valueChanged.connect(self._on_agc_noise_changed)
        self.hpf_enable.toggled.connect(self._on_hpf_toggle)
        self.hpf_cutoff_slider.valueChanged.connect(self._on_hpf_cutoff_changed)
        self.dfn_enable.toggled.connect(self._on_dfn_toggle)
        self.dfn_mix_slider.valueChanged.connect(self._on_dfn_mix_changed)
        self.dfn_post_slider.valueChanged.connect(self._on_dfn_post_changed)
        self.dfn_vad_link.toggled.connect(self._on_dfn_vad_link_toggle)
        self.dfn_vad_link_main.toggled.connect(self._on_dfn_vad_link_toggle)
        self.dfn_mix_speech_slider.valueChanged.connect(self._on_dfn_mix_speech_changed)
        self.dfn_mix_silence_slider.valueChanged.connect(self._on_dfn_mix_silence_changed)
        self.eq_enable.toggled.connect(self._on_eq_toggle)
        self.eq_low_slider.valueChanged.connect(self._on_eq_low_changed)
        self.eq_mid_slider.valueChanged.connect(self._on_eq_mid_changed)
        self.eq_high_slider.valueChanged.connect(self._on_eq_high_changed)
        self.cng_enable.toggled.connect(self._on_cng_toggle)
        self.cng_level_slider.valueChanged.connect(self._on_cng_level_changed)
        self.opus_bitrate.valueChanged.connect(self._on_opus_bitrate_changed)
        self.opus_fec.toggled.connect(self._on_opus_fec_toggle)
        self.opus_dtx.toggled.connect(self._on_opus_dtx_toggle)
        self.opus_loss.valueChanged.connect(self._on_opus_loss_changed)
        self.limiter_threshold_slider.valueChanged.connect(self._on_limiter_threshold_changed)
        self.limiter_attack_spin.valueChanged.connect(self._on_limiter_attack_changed)
        self.limiter_release_spin.valueChanged.connect(self._on_limiter_release_changed)
        
        # Connect Qt signals to slots for thread-safe UI updates
        self.connected_signal.connect(self._on_connected_slot)
        self.disconnected_signal.connect(self._on_disconnected_slot)
        self.media_error_signal.connect(self._on_media_error_slot)
        self.media_warning_signal.connect(self._on_media_warning_slot)
        
        # Set callbacks that emit signals (thread-safe)
        self.signaling.on_connected = self._on_connected_callback
        self.signaling.on_disconnected = self._on_disconnected_callback
        self.media.on_error = self._on_media_error_callback
        self.media.on_warning = self._on_media_warning_callback

    def _refresh_devices(self):
        current_input = self.input_combo.currentData()
        current_output = self.output_combo.currentData()
        current_input_text = self.input_combo.currentText()
        current_output_text = self.output_combo.currentText()
        sources, sinks = self.media.list_devices()
        signature = (
            tuple(sorted((str(dev.get("id")), dev.get("name", ""), dev.get("api", "")) for dev in sources)),
            tuple(sorted((str(dev.get("id")), dev.get("name", ""), dev.get("api", "")) for dev in sinks)),
        )
        if getattr(self, "_device_signature", None) == signature:
            return
        self._device_signature = signature
        self.input_combo.clear()
        self.output_combo.clear()
        
        self.input_combo.addItem("系统默认", None)
        self.output_combo.addItem("系统默认", None)
        
        for dev in sources:
            name = dev["name"]
            if "built-in" in name.lower() or "default" in name.lower():
                name = f"{name}"
            self.input_combo.addItem(name, dev)
        
        for dev in sinks:
            name = dev["name"]
            if "built-in" in name.lower() or "default" in name.lower():
                name = f"{name}"
            self.output_combo.addItem(name, dev)
        
        idx = self.input_combo.findData(current_input)
        if idx < 0 and current_input_text:
            idx = self.input_combo.findText(current_input_text)
        if idx >= 0:
            self.input_combo.setCurrentIndex(idx)
        else:
            self.input_combo.setCurrentIndex(0)

        idx = self.output_combo.findData(current_output)
        if idx < 0 and current_output_text:
            idx = self.output_combo.findText(current_output_text)
        if idx >= 0:
            self.output_combo.setCurrentIndex(idx)
        else:
            self.output_combo.setCurrentIndex(0)

    def _start_timer(self):
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update_metrics)
        self.timer.start(500)
        self.device_timer = QtCore.QTimer(self)
        self.device_timer.timeout.connect(self._refresh_devices)
        self.device_timer.start(3000)

    def _update_metrics(self):
        if self.media.pipeline:
            self.media.poll_metrics()
        data = self.metrics.snapshot()
        if self.media.dfn_active is False:
            self._set_metric(self.dfn_p50, self.metric_titles["dfn_p50"], "不可用")
            self._set_metric(self.dfn_p95, self.metric_titles["dfn_p95"], "不可用")
            self._set_metric(self.dfn_bypass, self.metric_titles["dfn_bypass"], "-")
            self._set_metric(self.dfn_auto_mix, self.metric_titles["dfn_auto_mix"], "-")
        else:
            self._set_metric(self.dfn_p50, self.metric_titles["dfn_p50"], self._fmt(data.get("dfn_p50_ms")))
            self._set_metric(self.dfn_p95, self.metric_titles["dfn_p95"], self._fmt(data.get("dfn_p95_ms")))
            self._set_metric(self.dfn_bypass, self.metric_titles["dfn_bypass"], str(data.get("dfn_bypass")))
            auto_mix = data.get("dfn_auto_mix")
            auto_bypass = data.get("dfn_auto_bypass")
            if auto_mix is None:
                auto_text = "-"
            else:
                auto_text = f"{float(auto_mix) * 100:.0f}%"
                if auto_bypass:
                    auto_text += "（自动降级）"
            self._set_metric(self.dfn_auto_mix, self.metric_titles["dfn_auto_mix"], auto_text)
        if self.media.aec_active is False:
            self._set_metric(self.aec_erle, self.metric_titles["aec_erle"], "不可用")
            self._set_metric(self.aec_erl, self.metric_titles["aec_erl"], "不可用")
            self._set_metric(self.aec_delay_metric, self.metric_titles["aec_delay"], "-")
        else:
            self._set_metric(self.aec_erle, self.metric_titles["aec_erle"], self._fmt(data.get("aec_erle_db")))
            self._set_metric(self.aec_erl, self.metric_titles["aec_erl"], self._fmt(data.get("aec_erl_db")))
            self._set_metric(self.aec_delay_metric, self.metric_titles["aec_delay"], self._fmt(data.get("aec_delay_ms")))
        self._set_metric(self.jitter_depth, self.metric_titles["jitter_depth"], self._fmt_jitter(data.get("jitter_depth"), data.get("jitter_kind")))
        self._set_metric(self.mic_send, self.metric_titles["mic_send"], self._fmt(data.get("mic_send_latency_ms")))
        self._set_metric(self.vad_prob, self.metric_titles["vad_prob"], self._fmt(data.get("vad_prob")))
        self._set_metric(self.vad_energy, self.metric_titles["vad_energy"], self._fmt(data.get("vad_energy_db")))
        queues = self._format_queue_depths(data.get("queue_depths", {}), data.get("queue_overruns", {}))
        self._set_metric(self.queue_depth, self.metric_titles["queue_depth"], queues)
        input_rate = data.get("input_sample_rate")
        target_rate = data.get("target_sample_rate")
        if input_rate or target_rate:
            rate_text = f"{input_rate or '-'} / {target_rate or '-'}"
        else:
            rate_text = "-"
        self._set_metric(self.sample_rate, self.metric_titles["sample_rate"], rate_text)
        
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

    def _format_queue_depths(self, queue_depths, queue_overruns):
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
        items = []
        for key, label in name_map.items():
            if key in queue_depths:
                over = queue_overruns.get(key, 0) if queue_overruns else 0
                if over:
                    items.append(f"{label}:{queue_depths[key]}(溢出{over})")
                else:
                    items.append(f"{label}:{queue_depths[key]}")
        return "、".join(items) if items else "-"

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
        QTabWidget::pane {
            border: 1px solid #E0E7F0;
            border-radius: 14px;
            background: #FFFFFF;
            padding: 8px;
        }
        QTabBar::tab {
            background: #E5E7EB;
            border-radius: 10px;
            padding: 6px 12px;
            margin-right: 6px;
            color: #4B5563;
        }
        QTabBar::tab:selected {
            background: #2563EB;
            color: #FFFFFF;
        }
        QTabWidget#advancedSubTabs::pane {
            border: 1px solid #E0E7F0;
            border-radius: 12px;
            background: #FFFFFF;
        }
        QTabWidget#advancedSubTabs QTabBar::tab {
            background: #E2E8F0;
            border-radius: 8px;
            padding: 4px 8px;
            margin-right: 6px;
            color: #475569;
            font-size: 11px;
            min-width: 80px;
        }
        QTabWidget#advancedSubTabs QTabBar::tab:selected {
            background: #1D4ED8;
            color: #FFFFFF;
            font-weight: 600;
        }
        QLineEdit, QComboBox, QSpinBox {
            background: #F8FAFC;
            border: 1px solid #D8E0EA;
            border-radius: 8px;
            padding: 6px 8px;
        }
        QFrame#pipelineCard {
            background: #FFFFFF;
            border: 1px solid #E0E7F0;
            border-radius: 12px;
        }
        QLabel#pipelineTitle {
            color: #4B5B71;
            font-weight: 600;
        }
        QLabel#pipelineFlow {
            color: #1F2937;
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
        if self.media.aec_active is not False:
            parts = [aec_text]
            if self.media.aec_erle_db is not None:
                parts.append(f"ERLE {self.media.aec_erle_db:.1f} dB")
            if self.media.aec_erl_db is not None:
                parts.append(f"ERL {self.media.aec_erl_db:.1f} dB")
            if self.media.aec_delay_estimate_ms is not None:
                parts.append(f"延迟 {self.media.aec_delay_estimate_ms} ms")
            aec_text = " | ".join(parts)
        dfn_text = self._format_flag(self.media.disable_dfn_env, self.media.dfn_active, self.media.dfn_enabled)
        self.aec_status.setText(aec_text)
        self.dfn_status.setText(dfn_text)
        self.hpf_status.setText(self._format_module(self.media.hpf_active, self.media.hpf_enabled))
        self.eq_status.setText(self._format_module(self.media.eq_active, self.media.eq_enabled))
        self.cng_status.setText(self._format_module(self.media.cng_active, self.media.cng_enabled))
        self.limiter_status.setText(self._format_module(self.media.limiter_active, True))
        self._update_pipeline_diagram()

    def _update_pipeline_diagram(self):
        def step(name, enabled=True, active=True):
            if active is False:
                return f"{name}(不可用)"
            if not enabled:
                return f"{name}(关)"
            return name

        hpf = step("HPF", self.media.hpf_enabled, self.media.hpf_active)
        aec = step("AEC3", self.media.aec_enabled, self.media.aec_active)
        dfn = step("DFN", self.media.dfn_enabled, self.media.dfn_active)
        cng = step("CNG", self.media.cng_enabled, self.media.cng_active)
        eq = step("EQ", self.media.eq_enabled, self.media.eq_active)
        limiter = step("Limiter", True, self.media.limiter_active)

        main_steps = ["采集", hpf, aec, dfn, cng, eq, limiter, "Opus", "RTP/UDP"]
        main_steps = [s for s in main_steps if s]
        main_text = " → ".join(main_steps)
        if self.media.is_listen_only:
            main_text += "（监听模式）"

        vad_text = "AEC3 后 → VAD"
        downlink_text = "RTP/UDP → Jitter → Opus → 播放"
        if self.media.aec_active is not False:
            downlink_text += "（AEC 参考）"

        self.pipeline_main.setText(f"主链路：{main_text}")
        self.pipeline_vad.setText(f"VAD 支路：{vad_text}")
        self.pipeline_downlink.setText(f"下行：{downlink_text}")
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

    def _format_module(self, active, enabled):
        if active is None:
            return "待初始化"
        if not active:
            return "不可用"
        if not enabled:
            return "已关闭"
        return "已启用"

    def _update_processing_controls(self):
        aec_available = self.media.aec_active is not False
        dfn_available = self.media.dfn_active is not False
        aec_enabled = self.media.aec_enabled
        dfn_enabled = self.media.dfn_enabled

        self._set_checkbox_silent(self.aec_enable, aec_enabled)
        self._set_checkbox_silent(self.aec_auto, self.media.aec_auto_delay)
        self._set_checkbox_silent(self.dfn_enable, dfn_enabled)
        self._set_checkbox_silent(self.agc_enable, self.media.agc_enabled)
        self._set_checkbox_silent(self.agc_input, self.media.agc_input_volume)
        self._set_checkbox_silent(self.hpf_enable, self.media.hpf_enabled)
        self._set_checkbox_silent(self.eq_enable, self.media.eq_enabled)
        self._set_checkbox_silent(self.cng_enable, self.media.cng_enabled)
        self._set_checkbox_silent(self.dfn_vad_link, self.media.dfn_vad_link)
        self._set_checkbox_silent(self.dfn_vad_link_main, self.media.dfn_vad_link)
        self._set_spin_silent(self.aec_delay, int(self.media.aec_delay_ms))
        self._set_slider_silent(self.dfn_mix_slider, int(self.media.dfn_mix * 100))
        self._set_slider_silent(self.dfn_post_slider, int(self.media.dfn_post_filter * 100))
        self._set_slider_silent(self.hpf_cutoff_slider, int(self.media.hpf_cutoff_hz))
        self._set_slider_silent(self.agc_headroom_slider, int(self.media.agc_headroom_db))
        self._set_slider_silent(self.agc_max_gain_slider, int(self.media.agc_max_gain_db))
        self._set_slider_silent(self.agc_initial_gain_slider, int(self.media.agc_initial_gain_db))
        self._set_slider_silent(self.agc_noise_slider, int(self.media.agc_max_noise_dbfs))
        self._set_slider_silent(self.eq_low_slider, int(self.media.eq_low_gain_db))
        self._set_slider_silent(self.eq_mid_slider, int(self.media.eq_mid_gain_db))
        self._set_slider_silent(self.eq_high_slider, int(self.media.eq_high_gain_db))
        self._set_slider_silent(self.cng_level_slider, int(self.media.cng_level_db))
        self._set_slider_silent(self.dfn_mix_speech_slider, int(self.media.dfn_mix_speech * 100))
        self._set_slider_silent(self.dfn_mix_silence_slider, int(self.media.dfn_mix_silence * 100))
        self._set_spin_silent(self.opus_bitrate, int(self.media.opus_bitrate))
        self._set_spin_silent(self.opus_loss, int(self.media.opus_packet_loss))
        self._set_checkbox_silent(self.opus_fec, self.media.opus_fec)
        self._set_checkbox_silent(self.opus_dtx, self.media.opus_dtx)
        self._set_slider_silent(self.limiter_threshold_slider, int(round(self.media.limiter_threshold_db)))
        self._set_spin_silent(self.limiter_attack_spin, int(round(self.media.limiter_attack_ms)))
        self._set_spin_silent(self.limiter_release_spin, int(round(self.media.limiter_release_ms)))
        self.dfn_mix_value.setText(f"{int(self.media.dfn_mix * 100)}%")
        self.dfn_post_value.setText(f"{int(self.media.dfn_post_filter * 100)}%")
        self.hpf_cutoff_value.setText(f"{int(self.media.hpf_cutoff_hz)} Hz")
        self.agc_headroom_value.setText(f"{int(self.media.agc_headroom_db)} dB")
        self.agc_max_gain_value.setText(f"{int(self.media.agc_max_gain_db)} dB")
        self.agc_initial_gain_value.setText(f"{int(self.media.agc_initial_gain_db)} dB")
        self.agc_noise_value.setText(f"{int(self.media.agc_max_noise_dbfs)} dBFS")
        self.eq_low_value.setText(f"{int(self.media.eq_low_gain_db)} dB")
        self.eq_mid_value.setText(f"{int(self.media.eq_mid_gain_db)} dB")
        self.eq_high_value.setText(f"{int(self.media.eq_high_gain_db)} dB")
        self.cng_level_value.setText(f"{int(self.media.cng_level_db)} dB")
        self.dfn_mix_speech_value.setText(f"{int(self.media.dfn_mix_speech * 100)}%")
        self.dfn_mix_silence_value.setText(f"{int(self.media.dfn_mix_silence * 100)}%")
        self.limiter_threshold_value.setText(f"{int(round(self.media.limiter_threshold_db))} dB")

        aec_controls_enabled = not self.media.disable_aec_env and aec_available
        dfn_controls_enabled = not self.media.disable_dfn_env and dfn_available
        self.aec_enable.setEnabled(aec_controls_enabled)
        self.aec_auto.setEnabled(aec_controls_enabled)
        self.aec_delay.setEnabled(aec_controls_enabled and not self.media.aec_auto_delay)
        self.dfn_enable.setEnabled(dfn_controls_enabled)
        self.dfn_mix_slider.setEnabled(dfn_controls_enabled)
        self.dfn_post_slider.setEnabled(dfn_controls_enabled)
        self.agc_enable.setEnabled(not self.media.disable_agc_env and aec_controls_enabled)
        self.agc_input.setEnabled(not self.media.disable_agc_env and aec_controls_enabled and self.media.agc_enabled)
        self.agc_headroom_slider.setEnabled(not self.media.disable_agc_env and aec_controls_enabled and self.media.agc_enabled)
        self.agc_max_gain_slider.setEnabled(not self.media.disable_agc_env and aec_controls_enabled and self.media.agc_enabled)
        self.agc_initial_gain_slider.setEnabled(not self.media.disable_agc_env and aec_controls_enabled and self.media.agc_enabled)
        self.agc_noise_slider.setEnabled(not self.media.disable_agc_env and aec_controls_enabled and self.media.agc_enabled)
        self.hpf_enable.setEnabled(self.media.hpf_active is not False)
        self.hpf_cutoff_slider.setEnabled(self.media.hpf_active is not False and self.media.hpf_enabled)
        self.eq_enable.setEnabled(self.media.eq_active is not False)
        self.eq_low_slider.setEnabled(self.media.eq_active is not False and self.media.eq_enabled)
        self.eq_mid_slider.setEnabled(self.media.eq_active is not False and self.media.eq_enabled)
        self.eq_high_slider.setEnabled(self.media.eq_active is not False and self.media.eq_enabled)
        self.cng_enable.setEnabled(self.media.cng_active is not False)
        self.cng_level_slider.setEnabled(self.media.cng_active is not False and self.media.cng_enabled)
        self.dfn_vad_link.setEnabled(dfn_controls_enabled)
        self.dfn_vad_link_main.setEnabled(dfn_controls_enabled)
        self.dfn_mix_speech_slider.setEnabled(dfn_controls_enabled and self.media.dfn_vad_link)
        self.dfn_mix_silence_slider.setEnabled(dfn_controls_enabled and self.media.dfn_vad_link)
        self.opus_bitrate.setEnabled(True)
        self.opus_fec.setEnabled(True)
        self.opus_dtx.setEnabled(True)
        self.opus_loss.setEnabled(True)
        limiter_enabled = self.media.limiter_active is not False
        self.limiter_threshold_slider.setEnabled(limiter_enabled)
        self.limiter_attack_spin.setEnabled(limiter_enabled)
        self.limiter_release_spin.setEnabled(limiter_enabled)

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

    def _on_agc_toggle(self, checked):
        if self.media.disable_agc_env:
            return
        self.media.set_processing_options(agc_enabled=checked)
        self._update_pipeline_flags()

    def _on_agc_input_toggle(self, checked):
        self.media.set_processing_options(agc_input_volume=checked)

    def _on_agc_headroom_changed(self, value):
        self.agc_headroom_value.setText(f"{value} dB")
        self.media.set_processing_options(agc_headroom_db=value)

    def _on_agc_max_gain_changed(self, value):
        self.agc_max_gain_value.setText(f"{value} dB")
        self.media.set_processing_options(agc_max_gain_db=value)

    def _on_agc_initial_gain_changed(self, value):
        self.agc_initial_gain_value.setText(f"{value} dB")
        self.media.set_processing_options(agc_initial_gain_db=value)

    def _on_agc_noise_changed(self, value):
        self.agc_noise_value.setText(f"{value} dBFS")
        self.media.set_processing_options(agc_max_noise_dbfs=value)

    def _on_hpf_toggle(self, checked):
        self.media.set_processing_options(hpf_enabled=checked)
        self._update_pipeline_flags()

    def _on_hpf_cutoff_changed(self, value):
        self.hpf_cutoff_value.setText(f"{value} Hz")
        self.media.set_processing_options(hpf_cutoff_hz=value)

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

    def _on_dfn_vad_link_toggle(self, checked):
        self.media.set_processing_options(dfn_vad_link=checked)
        self._set_checkbox_silent(self.dfn_vad_link, checked)
        self._set_checkbox_silent(self.dfn_vad_link_main, checked)

    def _on_dfn_mix_speech_changed(self, value):
        self.dfn_mix_speech_value.setText(f"{value}%")
        self.media.set_processing_options(dfn_mix_speech=value / 100.0)

    def _on_dfn_mix_silence_changed(self, value):
        self.dfn_mix_silence_value.setText(f"{value}%")
        self.media.set_processing_options(dfn_mix_silence=value / 100.0)

    def _on_eq_toggle(self, checked):
        self.media.set_processing_options(eq_enabled=checked)
        self._update_pipeline_flags()

    def _on_eq_low_changed(self, value):
        self.eq_low_value.setText(f"{value} dB")
        self.media.set_processing_options(eq_low_gain_db=value)

    def _on_eq_mid_changed(self, value):
        self.eq_mid_value.setText(f"{value} dB")
        self.media.set_processing_options(eq_mid_gain_db=value)

    def _on_eq_high_changed(self, value):
        self.eq_high_value.setText(f"{value} dB")
        self.media.set_processing_options(eq_high_gain_db=value)

    def _on_cng_toggle(self, checked):
        self.media.set_processing_options(cng_enabled=checked)
        self._update_pipeline_flags()

    def _on_cng_level_changed(self, value):
        self.cng_level_value.setText(f"{value} dB")
        self.media.set_processing_options(cng_level_db=value)

    def _on_opus_bitrate_changed(self, value):
        self.media.set_processing_options(opus_bitrate=value)

    def _on_opus_fec_toggle(self, checked):
        self.media.set_processing_options(opus_fec=checked)

    def _on_opus_dtx_toggle(self, checked):
        self.media.set_processing_options(opus_dtx=checked)

    def _on_opus_loss_changed(self, value):
        self.media.set_processing_options(opus_packet_loss=value)

    def _on_limiter_threshold_changed(self, value):
        self.limiter_threshold_value.setText(f"{value} dB")
        self.media.set_processing_options(limiter_threshold_db=value)

    def _on_limiter_attack_changed(self, value):
        self.media.set_processing_options(limiter_attack_ms=value)

    def _on_limiter_release_changed(self, value):
        self.media.set_processing_options(limiter_release_ms=value)

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
        self.signaling.set_local_rtp_port(local_port)
        self.signaling.start_listen(signaling_port, rtp_port=local_port)
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
        self.signaling.set_local_rtp_port(local_port)
        self.signaling.start_listen(signaling_port, rtp_port=local_port)
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
        if not self.is_listening:
            self.media.stop()

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
            rtp_port = None
            remote_ip = remote_addr[0] if len(remote_addr) > 0 else None
            signaling_port = remote_addr[1] if len(remote_addr) > 1 else None
            if len(remote_addr) > 2:
                rtp_port = remote_addr[2]
            if rtp_port is None:
                try:
                    rtp_port = int(self.remote_port.text())
                except ValueError:
                    if signaling_port is not None:
                        rtp_port = max(1, signaling_port - 1)
            if len(remote_addr) > 2 and rtp_port is not None:
                self.remote_port.setText(str(rtp_port))
            if rtp_port is None or remote_ip is None:
                self.status_label.setText("已连接")
                self._set_label_tone(self.status_label, "success")
                self.is_calling = True
                self.call_button.setEnabled(False)
                return
            if self.media.pipeline and self.media.is_listen_only:
                self.media.restart_with_remote(remote_ip, rtp_port)
            else:
                self.media.set_remote(remote_ip, rtp_port)
            self.media.set_send_enabled(True)
            self.status_label.setText(f"已连接 {remote_ip}:{rtp_port}")
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
        if not self.is_listening:
            self.media.stop()

    def _on_media_error_callback(self, message):
        self.media_error_signal.emit(message or "unknown")

    def _on_media_warning_callback(self, message):
        self.media_warning_signal.emit(message or "")

    @QtCore.Slot(str)
    def _on_media_error_slot(self, message):
        self.logger.error("Media error: %s", message)
        self.signaling.stop()
        self.status_label.setText("音频错误")
        self._set_label_tone(self.status_label, "warn")
        self.speaking_label.setText("否")
        self._set_label_tone(self.speaking_label, "muted")
        self.listen_button.setText("开始监听")
        self._set_button_active(self.listen_button, False)
        self.is_listening = False
        self.is_calling = False
        self.call_button.setEnabled(True)
        QtWidgets.QMessageBox.critical(self, "音频错误", f"音频管线错误：{message}")

    @QtCore.Slot(str)
    def _on_media_warning_slot(self, message):
        if not message:
            return
        self.logger.warning("Media warning: %s", message)
        QtWidgets.QMessageBox.information(self, "提示", message)

    def closeEvent(self, event):
        print("Closing application, cleaning up resources...")
        try:
            self.timer.stop()
            if hasattr(self, "device_timer"):
                self.device_timer.stop()
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

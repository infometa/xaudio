import logging
import os
import sys
import threading
import time

from gi.repository import Gst, GObject


class MediaEngine:
    def __init__(self, metrics, vad_manager):
        self.logger = logging.getLogger("Media")
        self.metrics = metrics
        self.vad = vad_manager
        self.pipeline = None
        self.bus = None
        self.udpsink = None
        self.udpsrc = None
        self.aec = None
        self.dfn = None
        self.limiter = None
        self.opusenc = None
        self.send_valve = None
        self.jitter = None
        self.jitter_latency_ms_default = self._env_int("TCHAT_JITTER_LATENCY_MS", 30)
        self.jitter_latency_ms = self.jitter_latency_ms_default
        self._last_jitter_adjust_ts = 0.0
        self.jitter_min_ms = self._env_int("TCHAT_JITTER_MIN_MS", 10)
        self.jitter_max_ms = self._env_int("TCHAT_JITTER_MAX_MS", 120)
        if self.jitter_min_ms > self.jitter_max_ms:
            self.jitter_min_ms, self.jitter_max_ms = self.jitter_max_ms, self.jitter_min_ms
        self.jitter_smoothing = self._clamp(self._env_float("TCHAT_JITTER_SMOOTHING", 0.9), 0.5, 0.98)
        self.jitter_adjust_interval = max(0.2, self._env_float("TCHAT_JITTER_ADJUST_INTERVAL", 2.0))
        self.queues = {}
        self.queue_overruns = {}
        self.clock = None
        self.base_time = None
        self.vad_sample_count = 0
        self.vad_sink = None
        self.lock = threading.Lock()
        self.is_listen_only = False
        self.last_local_port = None
        self.last_input_device = None
        self.last_output_device = None
        self.audio_src = None
        self.audio_sink = None
        self.target_sample_rate = self._env_int("TCHAT_TARGET_SAMPLE_RATE", 48000)
        self.input_sample_rate = None
        self.hpf = None
        self.hpf_enabled = self._env_flag_default("TCHAT_HPF_ENABLED", True)
        self.hpf_cutoff_hz = self._env_int("TCHAT_HPF_CUTOFF_HZ", 100)
        self.hpf_active = None
        self.eq = None
        self.eq_enabled = self._env_flag_default("TCHAT_EQ_ENABLED", True)
        self.eq_low_gain_db = self._env_float("TCHAT_EQ_LOW_DB", -2.0)
        self.eq_mid_gain_db = self._env_float("TCHAT_EQ_MID_DB", 2.0)
        self.eq_high_gain_db = self._env_float("TCHAT_EQ_HIGH_DB", 1.0)
        self.eq_active = None
        self.cng_enabled = self._env_flag_default("TCHAT_CNG_ENABLED", True)
        self.cng_level_db = self._env_float("TCHAT_CNG_LEVEL_DB", -62.0)
        self.cng_fade_ms = self._env_int("TCHAT_CNG_FADE_MS", 15)
        self.cng_mixer = None
        self.cng_src = None
        self.cng_volume = None
        self.cng_valve = None
        self.cng_conv = None
        self.cng_res = None
        self.cng_caps = None
        self.cng_active = None
        self.cng_current_level = 0.0
        self.cng_target_level = 0.0
        self.cng_last_update = 0.0
        self.disable_aec_env = self._env_flag("TCHAT_DISABLE_AEC")
        self.disable_dfn_env = self._env_flag("TCHAT_DISABLE_DFN")
        self.disable_agc_env = self._env_flag("TCHAT_DISABLE_AGC")
        self.aec_enabled = not self.disable_aec_env
        self.dfn_enabled = not self.disable_dfn_env
        self.agc_enabled = not self.disable_agc_env
        self.agc_input_volume = self._env_flag_default("TCHAT_AGC_INPUT_VOLUME", False)
        self.agc_headroom_db = self._env_float("TCHAT_AGC_HEADROOM_DB", 6.0)
        self.agc_max_gain_db = self._env_float("TCHAT_AGC_MAX_GAIN_DB", 30.0)
        self.agc_initial_gain_db = self._env_float("TCHAT_AGC_INITIAL_GAIN_DB", 10.0)
        self.agc_max_noise_dbfs = self._env_float("TCHAT_AGC_MAX_NOISE_DBFS", -50.0)
        self.aec_auto_delay = self._env_flag_default("TCHAT_AEC_AUTO_DELAY", True)
        self.aec_delay_ms = self._env_int("TCHAT_AEC_DELAY_MS", 0)
        self.dfn_mix = self._env_float("TCHAT_DFN_MIX", 0.85)
        self.dfn_post_filter = self._env_float("TCHAT_DFN_POST_FILTER", 0.1)
        self.dfn_vad_link = self._env_flag_default("TCHAT_DFN_VAD_LINK", True)
        self.dfn_mix_speech = self._env_float("TCHAT_DFN_MIX_SPEECH", 0.8)
        self.dfn_mix_silence = self._env_float("TCHAT_DFN_MIX_SILENCE", 1.0)
        self.dfn_mix_smoothing = self._env_float("TCHAT_DFN_MIX_SMOOTHING", 0.15)
        self.dfn_auto_mix = 1.0
        self.dfn_auto_bypass = False
        self.limiter_threshold_db = self._env_float("TCHAT_LIMITER_THRESHOLD_DB", -1.0)
        self.limiter_attack_ms = self._env_float("TCHAT_LIMITER_ATTACK_MS", 5.0)
        self.limiter_release_ms = self._env_float("TCHAT_LIMITER_RELEASE_MS", 80.0)
        self.opus_bitrate = self._env_int("TCHAT_OPUS_BITRATE", 48000)
        self.opus_packet_loss = self._env_int("TCHAT_OPUS_PACKET_LOSS", 5)
        self.opus_fec = self._env_flag_default("TCHAT_OPUS_FEC", True)
        self.opus_dtx = self._env_flag("TCHAT_OPUS_DTX")
        self.opus_complexity = self._env_int("TCHAT_OPUS_COMPLEXITY", 10)
        self.aec_active = None
        self.dfn_active = None
        self.limiter_active = None
        self.send_enabled = True
        self.on_error = None
        self.last_error = None
        self._handling_error = False

    def list_devices(self):
        sources = []
        sinks = []
        
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Audio/Source", None)
        monitor.add_filter("Audio/Sink", None)
        
        if not monitor.start():
            self.logger.warning("Failed to start GStreamer DeviceMonitor")
            return sources, sinks
        
        try:
            for device in monitor.get_devices():
                props = device.get_properties()
                device_class = device.get_device_class()
                display_name = device.get_display_name()
                
                device_id = None
                if props:
                    success, val = props.get_uint("device.api.coreaudio.id")
                    if success and val > 0:
                        device_id = val
                    else:
                        device_id_str = props.get_string("device.id")
                        if device_id_str:
                            try:
                                device_id = int(device_id_str)
                            except ValueError:
                                device_id = None
                
                device_info = {
                    "name": display_name,
                    "id": device_id,
                }
                
                if "Source" in device_class:
                    sources.append(device_info)
                elif "Sink" in device_class:
                    sinks.append(device_info)
                    
        except Exception as e:
            self.logger.warning(f"Failed to enumerate audio devices: {e}")
        finally:
            monitor.stop()
        
        return sources, sinks

    def start(self, local_port, remote_ip, remote_port, input_device=None, output_device=None):
        with self.lock:
            if self.pipeline:
                return
            self.pipeline = Gst.Pipeline.new("tchat")
            if self.pipeline is None:
                raise RuntimeError("Failed to create GStreamer pipeline")

        try:
            self.metrics.clear_runtime()
            self.last_error = None
            disable_aec = self._env_flag("TCHAT_DISABLE_AEC")
            disable_dfn = self._env_flag("TCHAT_DISABLE_DFN")
            disable_agc = self._env_flag("TCHAT_DISABLE_AGC")
            self.target_sample_rate = self._env_int("TCHAT_TARGET_SAMPLE_RATE", self.target_sample_rate)
            self.hpf_enabled = self._env_flag_default("TCHAT_HPF_ENABLED", self.hpf_enabled)
            self.hpf_cutoff_hz = self._env_int("TCHAT_HPF_CUTOFF_HZ", self.hpf_cutoff_hz)
            self.eq_enabled = self._env_flag_default("TCHAT_EQ_ENABLED", self.eq_enabled)
            self.eq_low_gain_db = self._env_float("TCHAT_EQ_LOW_DB", self.eq_low_gain_db)
            self.eq_mid_gain_db = self._env_float("TCHAT_EQ_MID_DB", self.eq_mid_gain_db)
            self.eq_high_gain_db = self._env_float("TCHAT_EQ_HIGH_DB", self.eq_high_gain_db)
            self.cng_enabled = self._env_flag_default("TCHAT_CNG_ENABLED", self.cng_enabled)
            self.cng_level_db = self._env_float("TCHAT_CNG_LEVEL_DB", self.cng_level_db)
            self.cng_fade_ms = self._env_int("TCHAT_CNG_FADE_MS", self.cng_fade_ms)
            self.dfn_vad_link = self._env_flag_default("TCHAT_DFN_VAD_LINK", self.dfn_vad_link)
            self.dfn_mix_speech = self._env_float("TCHAT_DFN_MIX_SPEECH", self.dfn_mix_speech)
            self.dfn_mix_silence = self._env_float("TCHAT_DFN_MIX_SILENCE", self.dfn_mix_silence)
            self.dfn_mix_smoothing = self._env_float("TCHAT_DFN_MIX_SMOOTHING", self.dfn_mix_smoothing)
            self.opus_bitrate = self._env_int("TCHAT_OPUS_BITRATE", self.opus_bitrate)
            self.opus_packet_loss = self._env_int("TCHAT_OPUS_PACKET_LOSS", self.opus_packet_loss)
            self.opus_fec = self._env_flag_default("TCHAT_OPUS_FEC", self.opus_fec)
            self.opus_dtx = self._env_flag("TCHAT_OPUS_DTX")
            self.agc_input_volume = self._env_flag_default("TCHAT_AGC_INPUT_VOLUME", self.agc_input_volume)
            self.agc_headroom_db = self._env_float("TCHAT_AGC_HEADROOM_DB", self.agc_headroom_db)
            self.agc_max_gain_db = self._env_float("TCHAT_AGC_MAX_GAIN_DB", self.agc_max_gain_db)
            self.agc_initial_gain_db = self._env_float("TCHAT_AGC_INITIAL_GAIN_DB", self.agc_initial_gain_db)
            self.agc_max_noise_dbfs = self._env_float("TCHAT_AGC_MAX_NOISE_DBFS", self.agc_max_noise_dbfs)
            if self.target_sample_rate < 8000 or self.target_sample_rate > 96000:
                self.logger.warning("Invalid target sample rate %s, using 48000", self.target_sample_rate)
                self.target_sample_rate = 48000
            self.disable_aec_env = disable_aec
            self.disable_dfn_env = disable_dfn
            self.disable_agc_env = disable_agc
            if disable_aec:
                self.aec_enabled = False
                self.agc_enabled = False
            if disable_dfn:
                self.dfn_enabled = False
            if disable_agc:
                self.agc_enabled = False
            self.is_listen_only = remote_ip is None or remote_port is None
            self.last_local_port = local_port
            self.last_input_device = input_device
            self.last_output_device = output_device
            self.logger.info("Media mode: %s", "listen-only" if self.is_listen_only else "full-duplex")
            if self.is_listen_only:
                self.cng_enabled = False
            if disable_aec:
                self.logger.info("AEC disabled via TCHAT_DISABLE_AEC")
            if disable_dfn:
                self.logger.info("DFN disabled via TCHAT_DISABLE_DFN")
            if disable_agc:
                self.logger.info("AGC disabled via TCHAT_DISABLE_AGC")

            src = self._make_audio_src(input_device)
            self.audio_src = src
            sink = None
            if not self.is_listen_only:
                sink = self._make_audio_sink(output_device)
                self.audio_sink = sink
                self._set_if_prop(sink, "sync", False)

            audconv1 = Gst.ElementFactory.make("audioconvert", "audconv1")
            audres1 = Gst.ElementFactory.make("audioresample", "audres1")
            self._set_if_prop(audres1, "quality", 10)
            caps1 = Gst.ElementFactory.make("capsfilter", "caps1")
            caps1.set_property(
                "caps",
                Gst.Caps.from_string(f"audio/x-raw,format=F32LE,rate={self.target_sample_rate},channels=1,layout=interleaved"),
            )
            self.hpf = self._make_hpf()
            self.hpf_active = bool(self.hpf and self.hpf.get_factory().get_name() != "identity")

            capture_q = self._make_queue("capture_q", max_buffers=10, leaky=False)
            
            if disable_aec:
                self.aec = None
            else:
                self.aec = Gst.ElementFactory.make("webrtcaec3", "aec")
                if not self.aec:
                    self.logger.warning("webrtcaec3 plugin not found; AEC disabled")
            self.aec_active = bool(self.aec) if not disable_aec else False
            if self.aec and self.aec.find_property("bypass"):
                self.aec.set_property("bypass", not self.aec_enabled)
            if self.aec and self.aec.find_property("stream-delay-ms"):
                self.aec.set_property("stream-delay-ms", int(self.aec_delay_ms))
            if self.aec and self.aec.find_property("auto-delay"):
                self.aec.set_property("auto-delay", bool(self.aec_auto_delay))
            if self.aec and self.aec.find_property("agc"):
                self.aec.set_property("agc", bool(self.agc_enabled))
            if self.aec and self.aec.find_property("agc-input-volume"):
                self.aec.set_property("agc-input-volume", bool(self.agc_input_volume))
            if self.aec and self.aec.find_property("agc-headroom-db"):
                self.aec.set_property("agc-headroom-db", float(self.agc_headroom_db))
            if self.aec and self.aec.find_property("agc-max-gain-db"):
                self.aec.set_property("agc-max-gain-db", float(self.agc_max_gain_db))
            if self.aec and self.aec.find_property("agc-initial-gain-db"):
                self.aec.set_property("agc-initial-gain-db", float(self.agc_initial_gain_db))
            if self.aec and self.aec.find_property("agc-max-noise-dbfs"):
                self.aec.set_property("agc-max-noise-dbfs", float(self.agc_max_noise_dbfs))
            if self.aec and self.aec.find_property("hpf"):
                self.aec.set_property("hpf", bool(self.hpf_enabled))

            # Tee for branching to VAD and encoder (before AEC)
            capture_tee = Gst.ElementFactory.make("tee", "capture_tee")
            vad_q = self._make_queue("vad_q", max_buffers=10, leaky="downstream")
            
            vad_conv = Gst.ElementFactory.make("audioconvert", "vad_conv")
            vad_res = Gst.ElementFactory.make("audioresample", "vad_res")
            self._set_if_prop(vad_res, "quality", 10)
            vad_f32_caps = Gst.ElementFactory.make("capsfilter", "vad_f32_caps")
            vad_f32_caps.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=16000,channels=1,layout=interleaved"),
            )
            vad_lpf = self._make_vad_lpf()
            vad_post_conv = Gst.ElementFactory.make("audioconvert", "vad_post_conv")
            vad_caps = Gst.ElementFactory.make("capsfilter", "vad_caps")
            vad_caps.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved"),
            )
            
            self.vad_sink = Gst.ElementFactory.make("appsink", "vad_sink")
            self.vad_sink.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved"),
            )
            self.vad_sink.set_property("emit-signals", True)
            self.vad_sink.set_property("sync", False)
            self.vad_sink.set_property("max-buffers", 10)
            self.vad_sink.set_property("drop", True)
            self.vad_sink.connect("new-sample", self._on_vad_sample)

            dfn_q = self._make_queue("dfn_q", max_buffers=10, leaky=False)
            dfn_in_caps = Gst.ElementFactory.make("capsfilter", "dfn_in_caps")
            dfn_in_caps.set_property(
                "caps",
                Gst.Caps.from_string(f"audio/x-raw,format=F32LE,rate={self.target_sample_rate},channels=1,layout=interleaved"),
            )
            self.dfn = Gst.ElementFactory.make("deepfilternet", "dfn")
            if disable_dfn:
                self.dfn = Gst.ElementFactory.make("identity", "dfn")
                if not self.dfn:
                    raise RuntimeError("Failed to create identity element for DFN bypass")
            else:
                if not self.dfn:
                    self.logger.warning("deepfilternet plugin not found; bypassing DFN")
                    self.dfn = Gst.ElementFactory.make("identity", "dfn")
                    if not self.dfn:
                        raise RuntimeError("Failed to create identity element for DFN bypass")
            self.dfn_active = bool(self.dfn and self.dfn.get_factory().get_name() != "identity") if not disable_dfn else False
            if self.dfn and self.dfn.find_property("bypass"):
                self.dfn.set_property("bypass", not self.dfn_enabled)
            if self.dfn and self.dfn.find_property("mix"):
                self.dfn.set_property("mix", self._clamp(self.dfn_mix, 0.0, 1.0))
            if self.dfn and self.dfn.find_property("post-filter"):
                self.dfn.set_property("post-filter", self._clamp(self.dfn_post_filter, 0.0, 1.0))
            models_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
            dfn_dir = os.path.join(models_root, "DeepFilterNet")
            if not disable_dfn:
                if os.path.isdir(dfn_dir) and os.path.exists(os.path.join(dfn_dir, "enc.onnx")):
                    self._set_if_prop(self.dfn, "model-dir", dfn_dir)
                else:
                    model_path = os.path.join(models_root, "deepfilternet.onnx")
                    self._set_if_prop(self.dfn, "model-path", model_path)

            post_dfn_q = self._make_queue("post_dfn_q", max_buffers=10, leaky=False)
            self.send_enabled = not self.is_listen_only
            self.send_valve = self._make_valve(drop=not self.send_enabled)
            self.eq = self._make_eq()
            self.eq_active = bool(self.eq and self.eq.get_factory().get_name() != "identity")
            self.cng_mixer, self.cng_src, self.cng_volume, self.cng_valve = self._make_cng()
            self.cng_active = bool(self.cng_mixer)
            self.limiter = self._make_limiter()
            self.limiter_active = bool(self.limiter and self.limiter.get_factory().get_name() != "identity")
            audconv_enc = Gst.ElementFactory.make("audioconvert", "audconv_enc")
            audres_enc = Gst.ElementFactory.make("audioresample", "audres_enc")
            self._set_if_prop(audres_enc, "quality", 10)
            enc_caps = Gst.ElementFactory.make("capsfilter", "enc_caps")
            enc_caps.set_property(
                "caps",
                Gst.Caps.from_string(f"audio/x-raw,format=S16LE,rate={self.target_sample_rate},channels=1,layout=interleaved"),
            )
            opusenc = Gst.ElementFactory.make("opusenc", "opusenc")
            self.opusenc = opusenc
            self._set_if_prop(opusenc, "bitrate", int(self.opus_bitrate))
            self._set_if_prop(opusenc, "frame-size", 10)
            self._set_if_prop(opusenc, "audio-type", "voice")
            self._set_if_prop(opusenc, "complexity", int(self.opus_complexity))
            self._set_if_prop(opusenc, "inband-fec", bool(self.opus_fec))
            self._set_if_prop(opusenc, "dtx", bool(self.opus_dtx))
            self._set_if_prop(opusenc, "packet-loss-percentage", int(self.opus_packet_loss))

            rtppay = Gst.ElementFactory.make("rtpopuspay", "rtppay")
            rtppay.set_property("pt", 96)

            if self.is_listen_only:
                self.udpsink = Gst.ElementFactory.make("fakesink", "rtp_sink")
            else:
                self.udpsink = Gst.ElementFactory.make("udpsink", "rtp_sink")
                self._set_if_prop(self.udpsink, "host", remote_ip or "127.0.0.1")
                if remote_port is not None:
                    self._set_if_prop(self.udpsink, "port", int(remote_port))
            self._set_if_prop(self.udpsink, "async", False)
            self._set_if_prop(self.udpsink, "sync", False)

            rtpdepay = None
            opusdec = None
            audconv2 = None
            audres2 = None
            caps2 = None
            playout_q = None
            playout_tee = None
            render_q = None
            if not self.is_listen_only:
                self.udpsrc = Gst.ElementFactory.make("udpsrc", "rtp_src")
                self.udpsrc.set_property("port", int(local_port))
                # Ensure pipeline stays live even before RTP arrives.
                self._set_if_prop(self.udpsrc, "is-live", True)
                self._set_if_prop(self.udpsrc, "do-timestamp", True)

                rtp_caps = Gst.Caps.from_string(
                    f"application/x-rtp,media=audio,encoding-name=OPUS,clock-rate={self.target_sample_rate},payload=96"
                )
                self.udpsrc.set_property("caps", rtp_caps)

                self.jitter = Gst.ElementFactory.make("rtpjitterbuffer", "jitter")
                self.jitter_latency_ms = int(self._clamp(self.jitter_latency_ms_default, self.jitter_min_ms, self.jitter_max_ms))
                self._last_jitter_adjust_ts = 0.0
                self.jitter.set_property("latency", self.jitter_latency_ms)
                self._set_if_prop(self.jitter, "drop-on-late", True)
                self.jitter.set_property("do-lost", True)

                rtpdepay = Gst.ElementFactory.make("rtpopusdepay", "rtpdepay")
                opusdec = Gst.ElementFactory.make("opusdec", "opusdec")
                audconv2 = Gst.ElementFactory.make("audioconvert", "audconv2")
                audres2 = Gst.ElementFactory.make("audioresample", "audres2")
                self._set_if_prop(audres2, "quality", 10)
                caps2 = Gst.ElementFactory.make("capsfilter", "caps2")
                caps2.set_property(
                    "caps",
                    Gst.Caps.from_string(f"audio/x-raw,format=F32LE,rate={self.target_sample_rate},channels=1,layout=interleaved"),
                )

                playout_q = self._make_queue("playout_q", max_buffers=10, leaky=False)
                playout_conv = Gst.ElementFactory.make("audioconvert", "playout_conv")
                playout_res = Gst.ElementFactory.make("audioresample", "playout_res")
                self._set_if_prop(playout_res, "quality", 10)
                playout_caps = Gst.ElementFactory.make("capsfilter", "playout_caps")
                playout_caps.set_property(
                    "caps",
                    Gst.Caps.from_string(f"audio/x-raw,format=S16LE,rate={self.target_sample_rate},channels=1,layout=interleaved"),
                )
                if self.aec:
                    playout_tee = Gst.ElementFactory.make("tee", "playout_tee")
                    render_q = self._make_queue("render_q", max_buffers=10, leaky=False)

            # Build elements dict
            elements = {
                "src": src,
                "audconv1": audconv1,
                "audres1": audres1,
                "caps1": caps1,
                "hpf": self.hpf,
                "capture_q": capture_q,
                "capture_tee": capture_tee,
                "vad_q": vad_q,
                "vad_conv": vad_conv,
                "vad_res": vad_res,
                "vad_f32_caps": vad_f32_caps,
                "vad_lpf": vad_lpf,
                "vad_post_conv": vad_post_conv,
                "vad_caps": vad_caps,
                "vad_sink": self.vad_sink,
                "dfn_q": dfn_q,
                "dfn_in_caps": dfn_in_caps,
                "dfn": self.dfn,
                "post_dfn_q": post_dfn_q,
                "send_valve": self.send_valve,
                "eq": self.eq,
                "limiter": self.limiter,
                "audconv_enc": audconv_enc,
                "audres_enc": audres_enc,
                "enc_caps": enc_caps,
                "opusenc": opusenc,
                "rtppay": rtppay,
                "udpsink": self.udpsink,
            }

            if self.cng_mixer:
                elements.update({
                    "cng_mixer": self.cng_mixer,
                    "cng_src": self.cng_src,
                    "cng_conv": self.cng_conv,
                    "cng_res": self.cng_res,
                    "cng_caps": self.cng_caps,
                    "cng_volume": self.cng_volume,
                    "cng_valve": self.cng_valve,
                })

            if self.aec:
                elements["aec"] = self.aec
                if playout_tee:
                    elements["playout_tee"] = playout_tee
                    elements["render_q"] = render_q

            if not self.is_listen_only:
                elements.update({
                    "udpsrc": self.udpsrc,
                    "jitter": self.jitter,
                    "rtpdepay": rtpdepay,
                    "opusdec": opusdec,
                    "audconv2": audconv2,
                    "audres2": audres2,
                    "caps2": caps2,
                    "playout_q": playout_q,
                    "playout_conv": playout_conv,
                    "playout_res": playout_res,
                    "playout_caps": playout_caps,
                    "sink": sink,
                })

            for name, element in elements.items():
                if element is None:
                    raise RuntimeError(f"Failed to create GStreamer element: {name}")
                self.pipeline.add(element)

            # Capture chain: src → tee (AEC runs on the main branch only)
            self._link_many_or_raise("capture", src, audconv1, audres1, caps1, self.hpf, capture_q, capture_tee)

            # VAD branch: tee → queue → convert → resample → caps → appsink
            # This works in both modes since it comes from capture path
            self._link_tee_src_to("capture→vad", capture_tee, vad_q)
            self._link_many_or_raise("vad", vad_q, vad_conv, vad_res, vad_f32_caps, vad_lpf, vad_post_conv, vad_caps, self.vad_sink)
            
            # Main branch: tee → queue → [AEC] → DFN → Limiter → Opus → RTP
            self._link_tee_src_to("capture→dfn", capture_tee, dfn_q)
            if self.aec:
                encoder_chain = [dfn_q, self.aec, dfn_in_caps, self.dfn, post_dfn_q, self.send_valve]
            else:
                encoder_chain = [dfn_q, dfn_in_caps, self.dfn, post_dfn_q, self.send_valve]

            if self.cng_mixer:
                self._link_many_or_raise("encoder_pre", *encoder_chain)
                self._link_to_mixer("cng_voice", self.send_valve, self.cng_mixer)
                self._link_many_or_raise("cng_noise", self.cng_src, self.cng_conv, self.cng_res, self.cng_caps, self.cng_volume, self.cng_valve)
                self._link_to_mixer("cng_mix", self.cng_valve, self.cng_mixer)
                self._link_many_or_raise(
                    "encoder_post",
                    self.cng_mixer,
                    self.eq,
                    self.limiter,
                    audconv_enc,
                    audres_enc,
                    enc_caps,
                    opusenc,
                    rtppay,
                    self.udpsink,
                )
            else:
                self._link_many_or_raise(
                    "encoder",
                    *encoder_chain,
                    self.eq,
                    self.limiter,
                    audconv_enc,
                    audres_enc,
                    enc_caps,
                    opusenc,
                    rtppay,
                    self.udpsink,
                )
            
            if not self.is_listen_only:
                if self.aec:
                    self._link_many_or_raise("decoder", self.udpsrc, self.jitter, rtpdepay, opusdec, audconv2, audres2, caps2, playout_tee)
                    self._link_tee_src_to("playout→sink", playout_tee, playout_q)
                    self._link_tee_src_to("playout→render", playout_tee, render_q)
                    self._link_many_or_raise("playout", playout_q, playout_conv, playout_res, playout_caps, sink)

                    render_pad = self.aec.get_request_pad("render_sink")
                    if render_pad:
                        render_src = render_q.get_static_pad("src")
                        self._pad_link_or_raise("render→aec", render_src, render_pad)
                    else:
                        self.logger.warning("AEC render pad not available")
                else:
                    self._link_many_or_raise("decoder", self.udpsrc, self.jitter, rtpdepay, opusdec, audconv2, audres2, caps2, playout_q, playout_conv, playout_res, playout_caps, sink)

            self.bus = self.pipeline.get_bus()
            self.bus.add_signal_watch()
            self.bus.connect("message", self._on_bus_message)
            
            self.logger.info("Verifying VAD sink configuration...")
            self.logger.info("  VAD sink emit-signals: %s", self.vad_sink.get_property("emit-signals"))
            self.logger.info("  VAD sink sync: %s", self.vad_sink.get_property("sync"))
            
            vad_sink_pad = self.vad_sink.get_static_pad("sink")
            if vad_sink_pad:
                peer = vad_sink_pad.get_peer()
                if peer:
                    self.logger.info("  VAD sink is linked to: %s", peer.get_parent().get_name())
                else:
                    self.logger.error("  VAD sink has NO PEER - not linked!")
            else:
                self.logger.error("  VAD sink has no sink pad!")

            udpsink_pad = self.udpsink.get_static_pad("sink")
            if udpsink_pad:
                udpsink_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_send_probe)
            else:
                self.logger.warning("Could not get udpsink pad for latency probe")

            self.pipeline.set_state(Gst.State.PLAYING)
            
            # Wait for pipeline to reach PLAYING state and collect messages
            ret = self.pipeline.get_state(timeout=5 * Gst.SECOND)
            if ret[0] == Gst.StateChangeReturn.SUCCESS:
                self.logger.info("Pipeline reached PLAYING state successfully")
            elif ret[0] == Gst.StateChangeReturn.ASYNC:
                self.logger.info("Pipeline state change is async (live pipeline)")
            else:
                self.logger.error("Pipeline failed to reach PLAYING state: %s", ret[0])
                # Try to get error from bus
                bus = self.pipeline.get_bus()
                msg = bus.timed_pop_filtered(1 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.WARNING)
                if msg:
                    if msg.type == Gst.MessageType.ERROR:
                        err, debug = msg.parse_error()
                        self.logger.error("Bus ERROR during startup: %s", err)
                        self.logger.error("Debug: %s", debug)
                    elif msg.type == Gst.MessageType.WARNING:
                        warn, debug = msg.parse_warning()
                        self.logger.warning("Bus WARNING during startup: %s", warn)
            
            # Verify VAD sink state
            vad_sink_state = self.vad_sink.get_state(timeout=1 * Gst.SECOND)
            self.logger.info("VAD sink state after pipeline start: %s -> %s", 
                            vad_sink_state[1].value_nick, vad_sink_state[2].value_nick)
            
            self.clock = self.pipeline.get_clock()
            self.base_time = self.pipeline.get_base_time()

            if self.aec_auto_delay:
                self._auto_update_aec_delay()

            self._log_sample_rate()
            
            # Export pipeline graph for debugging
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, "tchat_pipeline")
            self.logger.info("Pipeline graph exported to GST_DEBUG_DUMP_DOT_DIR (if set)")
            
            self.vad.start()
            self.logger.info("Pipeline started (local_port=%d, remote=%s:%s)", 
                            local_port, remote_ip or "none", remote_port or "none")
            self.logger.info("Audio devices: input=%s, output=%s", 
                            input_device or "default", output_device or "default")
        except Exception as exc:
            self.logger.exception("Failed to start pipeline: %s", exc)
            self.stop()
            raise

    def prewarm(self):
        if self.disable_dfn_env:
            return
        thread = threading.Thread(target=self._prewarm_worker, daemon=True)
        thread.start()

    def _prewarm_worker(self):
        try:
            dfn = Gst.ElementFactory.make("deepfilternet", None)
            if not dfn:
                return
            src = Gst.ElementFactory.make("audiotestsrc", None)
            conv = Gst.ElementFactory.make("audioconvert", None)
            res = Gst.ElementFactory.make("audioresample", None)
            caps = Gst.ElementFactory.make("capsfilter", None)
            caps.set_property(
                "caps",
                Gst.Caps.from_string(f"audio/x-raw,format=F32LE,rate={self.target_sample_rate},channels=1,layout=interleaved"),
            )
            sink = Gst.ElementFactory.make("fakesink", None)
            pipeline = Gst.Pipeline.new("tchat_prewarm")
            if not pipeline or not src or not conv or not res or not caps or not sink:
                return
            models_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
            dfn_dir = os.path.join(models_root, "DeepFilterNet")
            if os.path.isdir(dfn_dir) and os.path.exists(os.path.join(dfn_dir, "enc.onnx")):
                self._set_if_prop(dfn, "model-dir", dfn_dir)
            else:
                model_path = os.path.join(models_root, "deepfilternet.onnx")
                self._set_if_prop(dfn, "model-path", model_path)

            pipeline.add(src)
            pipeline.add(conv)
            pipeline.add(res)
            pipeline.add(caps)
            pipeline.add(dfn)
            pipeline.add(sink)
            if not src.link(conv):
                return
            if not conv.link(res):
                return
            if not res.link(caps):
                return
            if not caps.link(dfn):
                return
            if not dfn.link(sink):
                return

            self.logger.info("Prewarming DFN model...")
            pipeline.set_state(Gst.State.PAUSED)
            pipeline.get_state(timeout=3 * Gst.SECOND)
            pipeline.set_state(Gst.State.NULL)
            self.logger.info("DFN prewarm complete")
        except Exception as exc:
            self.logger.warning("DFN prewarm failed: %s", exc)

    def stop(self):
        if not self.pipeline:
            return
        self.vad.stop()
        
        if self.bus:
            self.bus.remove_signal_watch()
            self.bus = None

        self.pipeline.set_state(Gst.State.NULL)
        with self.lock:
            self.pipeline = None
            self.queues = {}
            self.queue_overruns = {}
            self.vad_sink = None
            self.vad_sample_count = 0
            self.aec = None
            self.dfn = None
            self.limiter = None
            self.eq = None
            self.hpf = None
            self.cng_mixer = None
            self.cng_src = None
            self.cng_volume = None
            self.cng_valve = None
            self.cng_conv = None
            self.cng_res = None
            self.cng_caps = None
            self.opusenc = None
            self.send_valve = None
            self.aec_active = None
            self.dfn_active = None
            self.limiter_active = None
            self.hpf_active = None
            self.eq_active = None
            self.cng_active = None
            self.udpsink = None
            self.udpsrc = None
            self.jitter = None
            self.audio_src = None
            self.audio_sink = None
        self.metrics.clear_runtime()
        self.logger.info("Pipeline stopped")

    def set_remote(self, ip, port):
        if self.udpsink:
            self._set_if_prop(self.udpsink, "host", ip)
            self._set_if_prop(self.udpsink, "port", int(port))

    def set_send_enabled(self, enabled):
        self.send_enabled = bool(enabled)
        if self.send_valve and self.send_valve.find_property("drop"):
            self.send_valve.set_property("drop", not self.send_enabled)

    def restart_with_remote(self, ip, port):
        if not self.pipeline:
            return
        if not self.is_listen_only:
            self.set_remote(ip, port)
            return
        self.logger.info("Restarting pipeline with remote %s:%s", ip, port)
        local_port = self.last_local_port
        input_id = self.last_input_device
        output_id = self.last_output_device
        self.stop()
        if local_port is None:
            return
        self.start(local_port, ip, port, input_id, output_id)

    def set_processing_options(
        self,
        aec_enabled=None,
        aec_delay_ms=None,
        aec_auto_delay=None,
        agc_enabled=None,
        agc_input_volume=None,
        agc_headroom_db=None,
        agc_max_gain_db=None,
        agc_initial_gain_db=None,
        agc_max_noise_dbfs=None,
        hpf_enabled=None,
        hpf_cutoff_hz=None,
        dfn_enabled=None,
        dfn_mix=None,
        dfn_post_filter=None,
        dfn_vad_link=None,
        dfn_mix_speech=None,
        dfn_mix_silence=None,
        eq_enabled=None,
        eq_low_gain_db=None,
        eq_mid_gain_db=None,
        eq_high_gain_db=None,
        cng_enabled=None,
        cng_level_db=None,
        limiter_threshold_db=None,
        limiter_attack_ms=None,
        limiter_release_ms=None,
        opus_bitrate=None,
        opus_fec=None,
        opus_dtx=None,
        opus_packet_loss=None,
    ):
        if aec_enabled is not None and not self.disable_aec_env:
            self.aec_enabled = bool(aec_enabled)
        if agc_enabled is not None and not self.disable_agc_env:
            self.agc_enabled = bool(agc_enabled)
        if agc_input_volume is not None:
            self.agc_input_volume = bool(agc_input_volume)
        if agc_headroom_db is not None:
            self.agc_headroom_db = float(agc_headroom_db)
        if agc_max_gain_db is not None:
            self.agc_max_gain_db = float(agc_max_gain_db)
        if agc_initial_gain_db is not None:
            self.agc_initial_gain_db = float(agc_initial_gain_db)
        if agc_max_noise_dbfs is not None:
            self.agc_max_noise_dbfs = float(agc_max_noise_dbfs)
        if hpf_enabled is not None:
            self.hpf_enabled = bool(hpf_enabled)
        if hpf_cutoff_hz is not None:
            try:
                self.hpf_cutoff_hz = int(hpf_cutoff_hz)
            except (TypeError, ValueError):
                pass
        if dfn_enabled is not None and not self.disable_dfn_env:
            self.dfn_enabled = bool(dfn_enabled)
        if dfn_vad_link is not None:
            self.dfn_vad_link = bool(dfn_vad_link)
        if dfn_mix_speech is not None:
            try:
                self.dfn_mix_speech = float(dfn_mix_speech)
            except (TypeError, ValueError):
                pass
        if dfn_mix_silence is not None:
            try:
                self.dfn_mix_silence = float(dfn_mix_silence)
            except (TypeError, ValueError):
                pass
        if aec_auto_delay is not None:
            self.aec_auto_delay = bool(aec_auto_delay)
        if aec_delay_ms is not None:
            if not self.aec_auto_delay:
                try:
                    self.aec_delay_ms = int(aec_delay_ms)
                except (TypeError, ValueError):
                    pass
        if dfn_mix is not None:
            try:
                self.dfn_mix = float(dfn_mix)
            except (TypeError, ValueError):
                pass
        if dfn_post_filter is not None:
            try:
                self.dfn_post_filter = float(dfn_post_filter)
            except (TypeError, ValueError):
                pass
        if eq_enabled is not None:
            self.eq_enabled = bool(eq_enabled)
        if eq_low_gain_db is not None:
            self.eq_low_gain_db = float(eq_low_gain_db)
        if eq_mid_gain_db is not None:
            self.eq_mid_gain_db = float(eq_mid_gain_db)
        if eq_high_gain_db is not None:
            self.eq_high_gain_db = float(eq_high_gain_db)
        if cng_enabled is not None:
            self.cng_enabled = bool(cng_enabled)
        if cng_level_db is not None:
            try:
                self.cng_level_db = float(cng_level_db)
            except (TypeError, ValueError):
                pass
        if limiter_threshold_db is not None:
            try:
                self.limiter_threshold_db = float(limiter_threshold_db)
            except (TypeError, ValueError):
                pass
        if limiter_attack_ms is not None:
            try:
                self.limiter_attack_ms = float(limiter_attack_ms)
            except (TypeError, ValueError):
                pass
        if limiter_release_ms is not None:
            try:
                self.limiter_release_ms = float(limiter_release_ms)
            except (TypeError, ValueError):
                pass
        if opus_bitrate is not None:
            try:
                self.opus_bitrate = int(opus_bitrate)
            except (TypeError, ValueError):
                pass
        if opus_fec is not None:
            self.opus_fec = bool(opus_fec)
        if opus_dtx is not None:
            self.opus_dtx = bool(opus_dtx)
        if opus_packet_loss is not None:
            try:
                self.opus_packet_loss = int(opus_packet_loss)
            except (TypeError, ValueError):
                pass

        if self.aec and self.aec.find_property("bypass"):
            self.aec.set_property("bypass", not self.aec_enabled)
        if self.aec and self.aec.find_property("agc"):
            self.aec.set_property("agc", bool(self.agc_enabled))
        if self.aec and self.aec.find_property("agc-input-volume"):
            self.aec.set_property("agc-input-volume", bool(self.agc_input_volume))
        if self.aec and self.aec.find_property("agc-headroom-db"):
            self.aec.set_property("agc-headroom-db", float(self.agc_headroom_db))
        if self.aec and self.aec.find_property("agc-max-gain-db"):
            self.aec.set_property("agc-max-gain-db", float(self.agc_max_gain_db))
        if self.aec and self.aec.find_property("agc-initial-gain-db"):
            self.aec.set_property("agc-initial-gain-db", float(self.agc_initial_gain_db))
        if self.aec and self.aec.find_property("agc-max-noise-dbfs"):
            self.aec.set_property("agc-max-noise-dbfs", float(self.agc_max_noise_dbfs))
        if self.aec and self.aec.find_property("hpf"):
            self.aec.set_property("hpf", bool(self.hpf_enabled))
        if self.aec and self.aec.find_property("auto-delay"):
            self.aec.set_property("auto-delay", bool(self.aec_auto_delay))
        if self.aec_auto_delay:
            self._auto_update_aec_delay()
        elif self.aec and self.aec.find_property("stream-delay-ms"):
            self.aec.set_property("stream-delay-ms", int(self.aec_delay_ms))
        if self.dfn and self.dfn.find_property("bypass"):
            self.dfn.set_property("bypass", not self.dfn_enabled)
        if self.dfn and self.dfn.find_property("mix"):
            self.dfn.set_property("mix", self._clamp(self.dfn_mix, 0.0, 1.0))
        if self.dfn and self.dfn.find_property("post-filter"):
            self.dfn.set_property("post-filter", self._clamp(self.dfn_post_filter, 0.0, 1.0))
        if self.eq and self.eq.find_property("band0"):
            gain = self.eq_low_gain_db if self.eq_enabled else 0.0
            self.eq.set_property("band0", float(gain))
        if self.eq and self.eq.find_property("band1"):
            gain = self.eq_mid_gain_db if self.eq_enabled else 0.0
            self.eq.set_property("band1", float(gain))
        if self.eq and self.eq.find_property("band2"):
            gain = self.eq_high_gain_db if self.eq_enabled else 0.0
            self.eq.set_property("band2", float(gain))
        if self.opusenc:
            self._set_if_prop(self.opusenc, "bitrate", int(self.opus_bitrate))
            self._set_if_prop(self.opusenc, "inband-fec", bool(self.opus_fec))
            self._set_if_prop(self.opusenc, "dtx", bool(self.opus_dtx))
            self._set_if_prop(self.opusenc, "packet-loss-percentage", int(self.opus_packet_loss))
        if self.hpf and self.hpf.find_property("cutoff"):
            cutoff = float(self.hpf_cutoff_hz if self.hpf_enabled else 20.0)
            self._set_if_prop(self.hpf, "cutoff", cutoff)
        if self.limiter:
            self._set_if_prop(self.limiter, "threshold", float(self.limiter_threshold_db))
            self._set_if_prop(self.limiter, "attack", float(self.limiter_attack_ms))
            self._set_if_prop(self.limiter, "release", float(self.limiter_release_ms))

    def poll_metrics(self):
        self._drain_bus_messages()
        with self.lock:
            queues = list(self.queues.items())
            jitter = self.jitter
        for name, queue in queues:
            try:
                depth = queue.get_property("current-level-buffers")
                self.metrics.update_queue_depth(name, depth)
            except Exception:
                continue
        self._update_mic_send_fallback()
        if jitter:
            try:
                stats = jitter.get_property("stats")
                metric = self._extract_jitter_metric(stats) if stats else None
                if metric is not None:
                    value, kind = metric
                    self.metrics.update_jitter_depth(value, kind)
                    self._adapt_jitter(value, kind)
            except Exception:
                pass
        self._update_vad_driven_processing()

    def _drain_bus_messages(self):
        if not self.bus:
            return
        mask = Gst.MessageType.ELEMENT | Gst.MessageType.ERROR | Gst.MessageType.WARNING
        while True:
            msg = self.bus.timed_pop_filtered(0, mask)
            if not msg:
                break
            self._on_bus_message(self.bus, msg)

    def _on_vad_sample(self, sink):
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.OK
        if self.vad_sample_count == 0:
            caps = sample.get_caps()
            if caps:
                self.logger.info("VAD sample caps: %s", caps.to_string())
        buffer = sample.get_buffer()
        if not buffer:
            return Gst.FlowReturn.OK
        success, info = buffer.map(Gst.MapFlags.READ)
        if success:
            try:
                self.vad_sample_count += 1
                if self.vad_sample_count == 1:
                    self.logger.info("VAD: First sample received from pipeline")
                elif self.vad_sample_count % 100 == 0:
                    self.logger.debug("VAD: Received %d samples from pipeline", self.vad_sample_count)
                self.vad.push_frame(bytes(info.data))
            finally:
                buffer.unmap(info)
        return Gst.FlowReturn.OK

    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.logger.error("Pipeline ERROR: %s", err)
            self.logger.error("Debug info: %s", debug)
            self.logger.error("Error domain: %s, code: %d", err.domain, err.code)
            src = message.src
            if src:
                self.logger.error("Error source: %s", src.get_name())
            self.last_error = f"{err}"
            if not self._handling_error:
                self._handling_error = True
                try:
                    if self.on_error:
                        self.on_error(self.last_error)
                finally:
                    self.stop()
                    self._handling_error = False
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            self.logger.warning("Pipeline WARNING: %s", err)
        elif t == Gst.MessageType.EOS:
            self.logger.info("Pipeline EOS")
        elif t == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline:
            old, new, pending = message.parse_state_changed()
            self.logger.debug("Pipeline state: %s -> %s (pending %s)", old.value_nick, new.value_nick, pending.value_nick)
        elif t == Gst.MessageType.ELEMENT:
            struct = message.get_structure()
            if struct and struct.get_name() == "dfn-stats":
                p50 = struct.get_value("p50_ms")
                p95 = struct.get_value("p95_ms")
                bypass = struct.get_value("bypass_count")
                auto_mix = struct.get_value("auto_mix") if struct.has_field("auto_mix") else None
                auto_bypass = struct.get_value("auto_bypass") if struct.has_field("auto_bypass") else None
                self.dfn_auto_mix = float(auto_mix) if auto_mix is not None else self.dfn_auto_mix
                self.dfn_auto_bypass = bool(auto_bypass) if auto_bypass is not None else self.dfn_auto_bypass
                self.metrics.update_dfn_stats(p50, p95, bypass, auto_mix=auto_mix, auto_bypass=auto_bypass)

    def _on_send_probe(self, pad, info):
        buf = info.get_buffer()
        if not buf:
            return Gst.PadProbeReturn.OK
        if not self.clock or self.base_time is None:
            if self.pipeline:
                self.clock = self.pipeline.get_clock()
                self.base_time = self.pipeline.get_base_time()
            if not self.clock or self.base_time is None:
                return Gst.PadProbeReturn.OK
        ts = buf.pts
        if ts == Gst.CLOCK_TIME_NONE:
            ts = buf.dts
        if ts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        now = self.clock.get_time() - self.base_time
        latency_ns = now - ts
        latency_ms = latency_ns / Gst.MSECOND
        self.metrics.update_mic_send_latency(latency_ms)
        return Gst.PadProbeReturn.OK

    def _make_queue(self, name, max_buffers=10, leaky="downstream"):
        queue = Gst.ElementFactory.make("queue", name)
        queue.set_property("max-size-buffers", max_buffers)
        queue.set_property("max-size-time", 0)
        queue.set_property("max-size-bytes", 0)
        if leaky is True or leaky == "downstream":
            leaky_mode = 2
        elif leaky == "upstream":
            leaky_mode = 1
        else:
            leaky_mode = 0
        queue.set_property("leaky", leaky_mode)
        queue.connect("overrun", self._on_queue_overrun)
        self.queues[name] = queue
        return queue

    def _on_queue_overrun(self, queue):
        name = queue.get_name() if queue else "unknown"
        with self.lock:
            count = self.queue_overruns.get(name, 0) + 1
            self.queue_overruns[name] = count
        if count == 1 or count % 50 == 0:
            self.logger.warning("Queue overrun: %s (%d)", name, count)
        self.metrics.update_queue_overrun(name, count)

    def _make_audio_src(self, device_id):
        if sys.platform.startswith("win"):
            src = Gst.ElementFactory.make("wasapisrc", "audiosrc")
            src.set_property("low-latency", True)
        elif sys.platform == "darwin":
            src = Gst.ElementFactory.make("osxaudiosrc", "audiosrc")
            if not src:
                self.logger.warning("osxaudiosrc not available, falling back to autoaudiosrc")
                src = Gst.ElementFactory.make("autoaudiosrc", "audiosrc")
        else:
            src = Gst.ElementFactory.make("autoaudiosrc", "audiosrc")
        
        if not src:
            raise RuntimeError("Failed to create audio source element")
        
        if device_id is not None:
            self._set_if_prop(src, "device", device_id)
        self._set_if_prop(src, "do-timestamp", True)
        
        self.logger.info(f"Created audio source: {src.get_factory().get_name()}")
        return src

    def _make_audio_sink(self, device_id):
        if sys.platform.startswith("win"):
            sink = Gst.ElementFactory.make("wasapisink", "audiosink")
            sink.set_property("low-latency", True)
        elif sys.platform == "darwin":
            sink = Gst.ElementFactory.make("osxaudiosink", "audiosink")
            if not sink:
                self.logger.warning("osxaudiosink not available, falling back to autoaudiosink")
                sink = Gst.ElementFactory.make("autoaudiosink", "audiosink")
        else:
            sink = Gst.ElementFactory.make("autoaudiosink", "audiosink")
        
        if not sink:
            raise RuntimeError("Failed to create audio sink element")
        
        if device_id is not None:
            self._set_if_prop(sink, "device", device_id)
        
        self.logger.info(f"Created audio sink: {sink.get_factory().get_name()}")
        return sink

    def _adapt_jitter(self, value, kind):
        if not self.jitter:
            return
        now = time.monotonic()
        if now - self._last_jitter_adjust_ts < self.jitter_adjust_interval:
            return
        if kind == "avg-jitter-ms":
            target = float(value) * 2.0 + 5.0
        elif kind == "queue":
            target = float(value) * 10.0 + 20.0
        else:
            return
        target = max(float(self.jitter_min_ms), min(float(self.jitter_max_ms), target))
        new_latency = int(round(self.jitter_latency_ms * self.jitter_smoothing + target * (1.0 - self.jitter_smoothing)))
        if abs(new_latency - self.jitter_latency_ms) >= 5:
            self.jitter_latency_ms = new_latency
            self.jitter.set_property("latency", self.jitter_latency_ms)
            self._last_jitter_adjust_ts = now
            if self.aec_auto_delay:
                self._auto_update_aec_delay()

    def _set_if_prop(self, element, prop, value):
        if not element:
            return
        if element.find_property(prop):
            if prop == "device" and isinstance(value, str):
                try:
                    value = int(value)
                except ValueError:
                    pass
            element.set_property(prop, value)

    def _update_mic_send_fallback(self):
        with self.lock:
            queue = self.queues.get("post_dfn_q") or self.queues.get("dfn_q")
        if not queue:
            return
        try:
            level_time = queue.get_property("current-level-time")
        except Exception:
            return
        if level_time is None:
            return
        latency_ms = level_time / Gst.MSECOND
        self.metrics.update_mic_send_latency(latency_ms)

    def _update_vad_driven_processing(self):
        data = self.metrics.snapshot()
        speaking = bool(data.get("vad_speaking"))
        if self.dfn_vad_link and self.dfn and self.dfn.find_property("mix"):
            target = self.dfn_mix_speech if speaking else self.dfn_mix_silence
            target = self._clamp(target, 0.0, 1.0)
            if abs(target - self.dfn_mix) > 0.005:
                smoothing = self._clamp(self.dfn_mix_smoothing, 0.05, 0.5)
                self.dfn_mix = (self.dfn_mix * (1.0 - smoothing)) + (target * smoothing)
                self.dfn.set_property("mix", self.dfn_mix)
        self._update_cng_state(speaking)

    def _update_cng_state(self, speaking):
        if not self.cng_mixer or not self.cng_volume or not self.cng_valve:
            return
        target_level = 0.0 if speaking or not self.cng_enabled else 10 ** (float(self.cng_level_db) / 20.0)
        now = time.monotonic()
        dt = now - self.cng_last_update if self.cng_last_update else 0.0
        self.cng_last_update = now
        if self.cng_fade_ms <= 0:
            self.cng_current_level = target_level
        else:
            fade_sec = max(0.01, self.cng_fade_ms / 1000.0)
            step = min(1.0, dt / fade_sec) if dt > 0 else 1.0
            self.cng_current_level += (target_level - self.cng_current_level) * step
        self._set_if_prop(self.cng_volume, "volume", float(self.cng_current_level))
        drop = self.cng_current_level < 1e-5
        self._set_if_prop(self.cng_valve, "drop", drop)

    def _extract_jitter_metric(self, stats):
        if not stats:
            return None
        for key in ("packets-in-queue", "queue-size", "queued-packets"):
            if stats.has_field(key):
                try:
                    return int(stats.get_value(key)), "queue"
                except Exception:
                    return None
        if stats.has_field("avg-jitter"):
            try:
                jitter_ns = int(stats.get_value("avg-jitter"))
                return jitter_ns / Gst.MSECOND, "avg-jitter-ms"
            except Exception:
                return None
        return None

    def _make_limiter(self):
        limiter = Gst.ElementFactory.make("audiolimiter", "limiter")
        if not limiter:
            self.logger.warning("audiolimiter plugin not found; limiter disabled")
            limiter = Gst.ElementFactory.make("identity", "limiter")
        self._set_if_prop(limiter, "threshold", float(self.limiter_threshold_db))
        self._set_if_prop(limiter, "attack", float(self.limiter_attack_ms))
        self._set_if_prop(limiter, "release", float(self.limiter_release_ms))
        return limiter

    def _make_hpf(self):
        hpf = Gst.ElementFactory.make("audiocheblimit", "hpf")
        if not hpf:
            self.logger.warning("audiocheblimit plugin not found; HPF disabled")
            return Gst.ElementFactory.make("identity", "hpf")
        try:
            if hpf.find_property("mode"):
                try:
                    hpf.set_property("mode", "high-pass")
                except Exception:
                    pass
            cutoff = float(self.hpf_cutoff_hz if self.hpf_enabled else 20.0)
            self._set_if_prop(hpf, "cutoff", cutoff)
            self._set_if_prop(hpf, "poles", 4)
        except Exception as exc:
            self.logger.warning("Failed to configure HPF: %s", exc)
        return hpf

    def _make_vad_lpf(self):
        lpf = Gst.ElementFactory.make("audiocheblimit", "vad_lpf")
        if not lpf:
            return Gst.ElementFactory.make("identity", "vad_lpf")
        try:
            if lpf.find_property("mode"):
                try:
                    lpf.set_property("mode", "low-pass")
                except Exception:
                    pass
            self._set_if_prop(lpf, "cutoff", 8000.0)
            self._set_if_prop(lpf, "poles", 4)
        except Exception:
            return Gst.ElementFactory.make("identity", "vad_lpf")
        return lpf

    def _make_eq(self):
        eq = Gst.ElementFactory.make("equalizer-3bands", "eq")
        if not eq:
            self.logger.warning("equalizer-3bands plugin not found; EQ disabled")
            return Gst.ElementFactory.make("identity", "eq")
        if self.eq_enabled:
            self._set_if_prop(eq, "band0", float(self.eq_low_gain_db))
            self._set_if_prop(eq, "band1", float(self.eq_mid_gain_db))
            self._set_if_prop(eq, "band2", float(self.eq_high_gain_db))
        else:
            self._set_if_prop(eq, "band0", 0.0)
            self._set_if_prop(eq, "band1", 0.0)
            self._set_if_prop(eq, "band2", 0.0)
        return eq

    def _make_cng(self):
        mixer = Gst.ElementFactory.make("audiomixer", "cng_mixer")
        if not mixer:
            self.logger.warning("audiomixer plugin not found; CNG disabled")
            return None, None, None, None
        src = Gst.ElementFactory.make("audiotestsrc", "cng_src")
        conv = Gst.ElementFactory.make("audioconvert", "cng_conv")
        res = Gst.ElementFactory.make("audioresample", "cng_res")
        caps = Gst.ElementFactory.make("capsfilter", "cng_caps")
        volume = Gst.ElementFactory.make("volume", "cng_volume")
        valve = Gst.ElementFactory.make("valve", "cng_valve")
        if not src or not conv or not res or not caps or not volume or not valve:
            self.logger.warning("CNG elements missing; disabled")
            return None, None, None, None
        self._set_if_prop(res, "quality", 10)
        self._set_if_prop(src, "is-live", True)
        self._set_if_prop(src, "wave", "white-noise")
        caps.set_property(
            "caps",
            Gst.Caps.from_string(f"audio/x-raw,format=F32LE,rate={self.target_sample_rate},channels=1,layout=interleaved"),
        )
        level = 10 ** (float(self.cng_level_db) / 20.0)
        self.cng_current_level = level if (self.cng_enabled and not self.is_listen_only) else 0.0
        self.cng_target_level = self.cng_current_level
        self._set_if_prop(volume, "volume", float(self.cng_current_level))
        self._set_if_prop(valve, "drop", self.cng_current_level < 1e-5)
        self.cng_conv = conv
        self.cng_res = res
        self.cng_caps = caps
        return mixer, src, volume, valve

    def _make_valve(self, drop=False):
        valve = Gst.ElementFactory.make("valve", "send_valve")
        if not valve:
            self.logger.warning("valve plugin not found; send gating disabled")
            valve = Gst.ElementFactory.make("identity", "send_valve")
        self._set_if_prop(valve, "drop", bool(drop))
        return valve

    def _env_flag(self, name):
        value = os.getenv(name)
        if value is None:
            return False
        value = value.strip().lower()
        return value not in ("", "0", "false", "no", "off")

    def _env_flag_default(self, name, default):
        value = os.getenv(name)
        if value is None:
            return default
        value = value.strip().lower()
        return value not in ("", "0", "false", "no", "off")

    def _env_int(self, name, default):
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _env_float(self, name, default):
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def _clamp(self, value, low, high):
        return max(low, min(high, float(value)))

    def _auto_update_aec_delay(self):
        if not self.aec or self.is_listen_only:
            return
        if self.aec.find_property("auto-delay"):
            self.aec.set_property("auto-delay", bool(self.aec_auto_delay))
            if not self.aec_auto_delay:
                return
        delay_ms = self._estimate_aec_delay_ms()
        if delay_ms is None:
            return
        self.aec_delay_ms = delay_ms
        if self.aec.find_property("stream-delay-ms"):
            self.aec.set_property("stream-delay-ms", int(self.aec_delay_ms))

    def _log_sample_rate(self):
        rate = None
        if self.audio_src:
            try:
                pad = self.audio_src.get_static_pad("src")
                if pad:
                    caps = pad.get_current_caps()
                    if caps:
                        rate = caps.get_structure(0).get_value("rate")
            except Exception:
                rate = None
        if rate:
            try:
                self.input_sample_rate = int(rate)
            except (TypeError, ValueError):
                self.input_sample_rate = None
        self.metrics.update_sample_rates(self.input_sample_rate, self.target_sample_rate)
        if self.input_sample_rate:
            if self.input_sample_rate != self.target_sample_rate:
                self.logger.warning("Input sample rate %s Hz, resampling to %s Hz", self.input_sample_rate, self.target_sample_rate)
            else:
                self.logger.info("Input sample rate %s Hz", self.input_sample_rate)

    def _estimate_aec_delay_ms(self):
        if not self.audio_src or not self.audio_sink:
            return None
        base_ms = 0.0
        for element in (self.audio_src, self.audio_sink):
            for prop in ("latency-time", "buffer-time"):
                if element.find_property(prop):
                    try:
                        # GstAudioBaseSrc/Sink report microseconds for latency/buffer time.
                        val = float(element.get_property(prop))
                        base_ms += val / 1000.0
                    except Exception:
                        continue
        if self.pipeline:
            try:
                ok, _live, min_lat, _max_lat = self.pipeline.query_latency()
                if ok:
                    base_ms = max(base_ms, float(min_lat) / Gst.MSECOND)
            except Exception:
                pass
        base_ms += float(self.jitter_latency_ms)
        base_ms += 10.0
        return int(self._clamp(round(base_ms), 0, 500))

    def _link_many_or_raise(self, label, *elems):
        """Link elements with explicit error reporting"""
        for i in range(len(elems) - 1):
            src = elems[i]
            sink = elems[i + 1]
            if not src.link(sink):
                src_caps = src.get_static_pad("src").query_caps(None) if src.get_static_pad("src") else "N/A"
                sink_caps = sink.get_static_pad("sink").query_caps(None) if sink.get_static_pad("sink") else "N/A"
                self.logger.error(f"Failed to link {src.get_name()} → {sink.get_name()}")
                self.logger.error(f"  Source caps: {src_caps}")
                self.logger.error(f"  Sink caps: {sink_caps}")
                raise RuntimeError(f"Link failed ({label}): {src.get_name()} → {sink.get_name()}")

    def _pad_link_or_raise(self, label, src_pad, sink_pad):
        """Link pads with explicit error reporting"""
        ret = src_pad.link(sink_pad)
        if ret != Gst.PadLinkReturn.OK:
            raise RuntimeError(f"Pad link failed ({label}): {ret.value_nick}")

    def _link_tee_src_to(self, label, tee, sink_elem, sink_pad_name="sink"):
        """Link tee source pad (request pad) to sink element"""
        tee_src = tee.get_request_pad("src_%u")
        if tee_src is None:
            raise RuntimeError(f"Failed to request tee src pad ({label})")
        sink_pad = sink_elem.get_static_pad(sink_pad_name)
        if sink_pad is None:
            raise RuntimeError(f"Missing sink pad {sink_pad_name} ({label})")
        self._pad_link_or_raise(label, tee_src, sink_pad)
        return tee_src

    def _link_to_mixer(self, label, src_elem, mixer):
        if not mixer:
            raise RuntimeError(f"Missing mixer ({label})")
        src_pad = src_elem.get_static_pad("src")
        if src_pad is None:
            raise RuntimeError(f"Missing src pad ({label})")
        sink_pad = mixer.get_request_pad("sink_%u")
        if sink_pad is None:
            raise RuntimeError(f"Failed to request mixer sink pad ({label})")
        self._pad_link_or_raise(label, src_pad, sink_pad)
        return sink_pad

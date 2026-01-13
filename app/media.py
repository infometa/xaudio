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
        self.send_valve = None
        self.jitter = None
        self.jitter_latency_ms_default = self._env_int("TCHAT_JITTER_LATENCY_MS", 40)
        self.jitter_latency_ms = self.jitter_latency_ms_default
        self._last_jitter_adjust_ts = 0.0
        self.queues = {}
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
        self.disable_aec_env = self._env_flag("TCHAT_DISABLE_AEC")
        self.disable_dfn_env = self._env_flag("TCHAT_DISABLE_DFN")
        self.aec_enabled = not self.disable_aec_env
        self.dfn_enabled = not self.disable_dfn_env
        self.aec_auto_delay = self._env_flag_default("TCHAT_AEC_AUTO_DELAY", True)
        self.aec_delay_ms = self._env_int("TCHAT_AEC_DELAY_MS", 0)
        self.dfn_mix = self._env_float("TCHAT_DFN_MIX", 1.0)
        self.dfn_post_filter = self._env_float("TCHAT_DFN_POST_FILTER", 0.0)
        self.limiter_threshold_db = self._env_float("TCHAT_LIMITER_THRESHOLD_DB", -1.0)
        self.limiter_attack_ms = self._env_float("TCHAT_LIMITER_ATTACK_MS", 5.0)
        self.limiter_release_ms = self._env_float("TCHAT_LIMITER_RELEASE_MS", 50.0)
        self.opus_bitrate = self._env_int("TCHAT_OPUS_BITRATE", 24000)
        self.opus_packet_loss = self._env_int("TCHAT_OPUS_PACKET_LOSS", 0)
        self.opus_fec = self._env_flag("TCHAT_OPUS_FEC")
        self.opus_dtx = self._env_flag("TCHAT_OPUS_DTX")
        self.opus_complexity = self._env_int("TCHAT_OPUS_COMPLEXITY", 10)
        self.aec_active = None
        self.dfn_active = None
        self.send_enabled = True

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
            disable_aec = self._env_flag("TCHAT_DISABLE_AEC")
            disable_dfn = self._env_flag("TCHAT_DISABLE_DFN")
            self.disable_aec_env = disable_aec
            self.disable_dfn_env = disable_dfn
            if disable_aec:
                self.aec_enabled = False
            if disable_dfn:
                self.dfn_enabled = False
            self.is_listen_only = remote_ip is None or remote_port is None
            self.last_local_port = local_port
            self.last_input_device = input_device
            self.last_output_device = output_device
            self.logger.info("Media mode: %s", "listen-only" if self.is_listen_only else "full-duplex")
            if disable_aec:
                self.logger.info("AEC disabled via TCHAT_DISABLE_AEC")
            if disable_dfn:
                self.logger.info("DFN disabled via TCHAT_DISABLE_DFN")

            src = self._make_audio_src(input_device)
            self.audio_src = src
            sink = None
            if not self.is_listen_only:
                sink = self._make_audio_sink(output_device)
                self.audio_sink = sink
                self._set_if_prop(sink, "sync", False)

            audconv1 = Gst.ElementFactory.make("audioconvert", "audconv1")
            audres1 = Gst.ElementFactory.make("audioresample", "audres1")
            caps1 = Gst.ElementFactory.make("capsfilter", "caps1")
            caps1.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"),
            )

            capture_q = self._make_queue("capture_q", max_buffers=10, leaky=True)
            
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

            # Tee for branching to VAD and encoder (before AEC)
            capture_tee = Gst.ElementFactory.make("tee", "capture_tee")
            vad_q = self._make_queue("vad_q", max_buffers=10, leaky=True)
            
            vad_conv = Gst.ElementFactory.make("audioconvert", "vad_conv")
            vad_res = Gst.ElementFactory.make("audioresample", "vad_res")
            self._set_if_prop(vad_res, "quality", 4)
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

            dfn_q = self._make_queue("dfn_q", max_buffers=10, leaky=True)
            dfn_in_caps = Gst.ElementFactory.make("capsfilter", "dfn_in_caps")
            dfn_in_caps.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"),
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

            post_dfn_q = self._make_queue("post_dfn_q", max_buffers=10, leaky=True)
            self.send_enabled = not self.is_listen_only
            self.send_valve = self._make_valve(drop=not self.send_enabled)
            self.limiter = self._make_limiter()
            audconv_enc = Gst.ElementFactory.make("audioconvert", "audconv_enc")
            audres_enc = Gst.ElementFactory.make("audioresample", "audres_enc")
            enc_caps = Gst.ElementFactory.make("capsfilter", "enc_caps")
            enc_caps.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved"),
            )
            opusenc = Gst.ElementFactory.make("opusenc", "opusenc")
            self._set_if_prop(opusenc, "bitrate", int(self.opus_bitrate))
            self._set_if_prop(opusenc, "frame-size", 10)
            self._set_if_prop(opusenc, "audio-type", "voice")
            self._set_if_prop(opusenc, "complexity", int(self.opus_complexity))
            self._set_if_prop(opusenc, "inband-fec", bool(self.opus_fec))
            self._set_if_prop(opusenc, "dtx", bool(self.opus_dtx))
            if self.opus_packet_loss > 0:
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
                    "application/x-rtp,media=audio,encoding-name=OPUS,clock-rate=48000,payload=96"
                )
                self.udpsrc.set_property("caps", rtp_caps)

                self.jitter = Gst.ElementFactory.make("rtpjitterbuffer", "jitter")
                self.jitter_latency_ms = self.jitter_latency_ms_default
                self._last_jitter_adjust_ts = 0.0
                self.jitter.set_property("latency", self.jitter_latency_ms)
                self._set_if_prop(self.jitter, "drop-on-late", True)
                self.jitter.set_property("do-lost", True)

                rtpdepay = Gst.ElementFactory.make("rtpopusdepay", "rtpdepay")
                opusdec = Gst.ElementFactory.make("opusdec", "opusdec")
                audconv2 = Gst.ElementFactory.make("audioconvert", "audconv2")
                audres2 = Gst.ElementFactory.make("audioresample", "audres2")
                caps2 = Gst.ElementFactory.make("capsfilter", "caps2")
                caps2.set_property(
                    "caps",
                    Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"),
                )

                playout_q = self._make_queue("playout_q", max_buffers=10, leaky=True)
                playout_conv = Gst.ElementFactory.make("audioconvert", "playout_conv")
                playout_res = Gst.ElementFactory.make("audioresample", "playout_res")
                playout_caps = Gst.ElementFactory.make("capsfilter", "playout_caps")
                playout_caps.set_property(
                    "caps",
                    Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved"),
                )
                if self.aec:
                    playout_tee = Gst.ElementFactory.make("tee", "playout_tee")
                    render_q = self._make_queue("render_q", max_buffers=10, leaky=True)

            # Build elements dict
            elements = {
                "src": src,
                "audconv1": audconv1,
                "audres1": audres1,
                "caps1": caps1,
                "capture_q": capture_q,
                "capture_tee": capture_tee,
                "vad_q": vad_q,
                "vad_conv": vad_conv,
                "vad_res": vad_res,
                "vad_caps": vad_caps,
                "vad_sink": self.vad_sink,
                "dfn_q": dfn_q,
                "dfn_in_caps": dfn_in_caps,
                "dfn": self.dfn,
                "post_dfn_q": post_dfn_q,
                "send_valve": self.send_valve,
                "limiter": self.limiter,
                "audconv_enc": audconv_enc,
                "audres_enc": audres_enc,
                "enc_caps": enc_caps,
                "opusenc": opusenc,
                "rtppay": rtppay,
                "udpsink": self.udpsink,
            }

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
            self._link_many_or_raise("capture", src, audconv1, audres1, caps1, capture_q, capture_tee)

            # VAD branch: tee → queue → convert → resample → caps → appsink
            # This works in both modes since it comes from capture path
            self._link_tee_src_to("capture→vad", capture_tee, vad_q)
            self._link_many_or_raise("vad", vad_q, vad_conv, vad_res, vad_caps, self.vad_sink)
            
            # Main branch: tee → queue → [AEC] → DFN → Limiter → Opus → RTP
            self._link_tee_src_to("capture→dfn", capture_tee, dfn_q)
            if self.aec:
                self._link_many_or_raise(
                    "encoder",
                    dfn_q,
                    self.aec,
                    dfn_in_caps,
                    self.dfn,
                    post_dfn_q,
                    self.send_valve,
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
                    dfn_q,
                    dfn_in_caps,
                    self.dfn,
                    post_dfn_q,
                    self.send_valve,
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
                Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"),
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
            self.vad_sink = None
            self.vad_sample_count = 0
            self.aec = None
            self.dfn = None
            self.limiter = None
            self.send_valve = None
            self.aec_active = None
            self.dfn_active = None
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

    def set_processing_options(self, aec_enabled=None, aec_delay_ms=None, aec_auto_delay=None, dfn_enabled=None, dfn_mix=None, dfn_post_filter=None):
        if aec_enabled is not None and not self.disable_aec_env:
            self.aec_enabled = bool(aec_enabled)
        if dfn_enabled is not None and not self.disable_dfn_env:
            self.dfn_enabled = bool(dfn_enabled)
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

        if self.aec and self.aec.find_property("bypass"):
            self.aec.set_property("bypass", not self.aec_enabled)
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
                self.metrics.update_dfn_stats(p50, p95, bypass)

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

    def _make_queue(self, name, max_buffers=10, leaky=True):
        queue = Gst.ElementFactory.make("queue", name)
        queue.set_property("max-size-buffers", max_buffers)
        queue.set_property("max-size-time", 0)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("leaky", 2 if leaky else 0)
        self.queues[name] = queue
        return queue

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
        now = time.time()
        if now - self._last_jitter_adjust_ts < 1.0:
            return
        if kind == "avg-jitter-ms":
            target = max(20.0, min(200.0, float(value) * 2.0 + 5.0))
        elif kind == "queue":
            target = max(20.0, min(200.0, float(value) * 10.0 + 20.0))
        else:
            return
        new_latency = int(round(self.jitter_latency_ms * 0.8 + target * 0.2))
        if abs(new_latency - self.jitter_latency_ms) >= 2:
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

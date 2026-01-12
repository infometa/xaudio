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
        self.jitter = None
        self.jitter_latency_ms = 40
        self.queues = {}
        self.clock = None
        self.base_time = None
        self.vad_sample_count = 0
        self.vad_sink = None
        self.lock = threading.Lock()

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

        # CRITICAL: Detect listen-only mode early to conditionally build pipeline
        is_listen_only = (remote_ip is None or remote_port is None)
        
        src = self._make_audio_src(input_device)
        sink = self._make_audio_sink(output_device)

        audconv1 = Gst.ElementFactory.make("audioconvert", "audconv1")
        audres1 = Gst.ElementFactory.make("audioresample", "audres1")
        caps1 = Gst.ElementFactory.make("capsfilter", "caps1")
        caps1.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"),
        )

        capture_q = self._make_queue("capture_q")
        
        # AEC is only needed when we have playout (echo to cancel)
        # In listen-only mode, skip AEC since there's no speaker output
        if not is_listen_only:
            self.aec = Gst.ElementFactory.make("webrtcaec3", "aec")
            if not self.aec:
                raise RuntimeError("webrtcaec3 plugin not found")

        # Tee for branching to VAD and encoder (after AEC in full mode, after caps in listen-only)
        capture_tee = Gst.ElementFactory.make("tee", "capture_tee")
        vad_q = self._make_queue("vad_q")
        vad_q.set_property("leaky", 2)  # downstream
        vad_q.set_property("max-size-buffers", 0)
        vad_q.set_property("max-size-bytes", 0)
        vad_q.set_property("max-size-time", 0)
        
        vad_conv = Gst.ElementFactory.make("audioconvert", "vad_conv")
        vad_res = Gst.ElementFactory.make("audioresample", "vad_res")
        vad_caps = Gst.ElementFactory.make("capsfilter", "vad_caps")
        vad_caps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved"),
        )
        
        self.vad_sink = Gst.ElementFactory.make("appsink", "vad_sink")
        self.vad_sink.set_property("emit-signals", True)
        self.vad_sink.set_property("sync", False)
        self.vad_sink.set_property("max-buffers", 10)
        self.vad_sink.set_property("drop", True)
        self.vad_sink.connect("new-sample", self._on_vad_sample)

        dfn_q = self._make_queue("dfn_q")
        dfn_in_caps = Gst.ElementFactory.make("capsfilter", "dfn_in_caps")
        dfn_in_caps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"),
        )
        self.dfn = Gst.ElementFactory.make("deepfilternet", "dfn")
        if not self.dfn:
            raise RuntimeError("deepfilternet plugin not found")
        models_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
        dfn_dir = os.path.join(models_root, "DeepFilterNet")
        if os.path.isdir(dfn_dir) and os.path.exists(os.path.join(dfn_dir, "enc.onnx")):
            self.dfn.set_property("model-dir", dfn_dir)
        else:
            model_path = os.path.join(models_root, "deepfilternet.onnx")
            self.dfn.set_property("model-path", model_path)

        post_dfn_q = self._make_queue("post_dfn_q")
        audconv_enc = Gst.ElementFactory.make("audioconvert", "audconv_enc")
        audres_enc = Gst.ElementFactory.make("audioresample", "audres_enc")
        enc_caps = Gst.ElementFactory.make("capsfilter", "enc_caps")
        enc_caps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved"),
        )
        opusenc = Gst.ElementFactory.make("opusenc", "opusenc")
        opusenc.set_property("bitrate", 24000)
        opusenc.set_property("frame-size", 10)
        opusenc.set_property("audio-type", "voice")

        rtppay = Gst.ElementFactory.make("rtpopuspay", "rtppay")
        rtppay.set_property("pt", 96)

        self.udpsink = Gst.ElementFactory.make("udpsink", "rtp_sink")
        self.udpsink.set_property("host", remote_ip or "127.0.0.1")
        self.udpsink.set_property("port", remote_port or 9)
        self.udpsink.set_property("async", False)
        self.udpsink.set_property("sync", False)

        self.udpsrc = Gst.ElementFactory.make("udpsrc", "rtp_src")
        self.udpsrc.set_property("port", int(local_port))
        
        is_listen_only = (remote_ip is None or remote_port is None)
        
        rtp_capsfilter = None
        rtpdepay = None
        opusdec = None
        audconv2 = None
        audres2 = None
        caps2 = None
        playout_tee = None
        playout_q = None
        render_q = None
        
        if not is_listen_only:
            rtp_caps = Gst.Caps.from_string(
                "application/x-rtp,media=audio,encoding-name=OPUS,clock-rate=48000,payload=96"
            )
            rtp_capsfilter = Gst.ElementFactory.make("capsfilter", "rtp_caps")
            rtp_capsfilter.set_property("caps", rtp_caps)

            self.jitter = Gst.ElementFactory.make("rtpjitterbuffer", "jitter")
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

            playout_tee = Gst.ElementFactory.make("tee", "playout_tee")
            playout_q = self._make_queue("playout_q")
            render_q = self._make_queue("render_q")

        # Build elements dict - only include decoder elements if not listen_only
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
            "audconv_enc": audconv_enc,
            "audres_enc": audres_enc,
            "enc_caps": enc_caps,
            "opusenc": opusenc,
            "rtppay": rtppay,
            "udpsink": self.udpsink,
        }
        
        if not is_listen_only:
            elements.update({
                "aec": self.aec,
                "udpsrc": self.udpsrc,
                "rtp_capsfilter": rtp_capsfilter,
                "jitter": self.jitter,
                "rtpdepay": rtpdepay,
                "opusdec": opusdec,
                "audconv2": audconv2,
                "audres2": audres2,
                "caps2": caps2,
                "playout_tee": playout_tee,
                "playout_q": playout_q,
                "render_q": render_q,
                "sink": sink,
            })
        
        for name, element in elements.items():
            if element is None:
                raise RuntimeError(f"Failed to create GStreamer element: {name}")
            self.pipeline.add(element)

        # Capture chain: src → [AEC (if not listen-only)] → tee
        if is_listen_only:
            # Listen-only: skip AEC (no echo to cancel)
            self._link_many_or_raise("capture", src, audconv1, audres1, caps1, capture_q, capture_tee)
        else:
            # Full mode: include AEC
            self._link_many_or_raise("capture", src, audconv1, audres1, caps1, capture_q, self.aec, capture_tee)

        # Encoder chain: DFN → Opus → RTP
        self._link_many_or_raise("encoder", dfn_q, dfn_in_caps, self.dfn, post_dfn_q, audconv_enc, audres_enc, enc_caps, opusenc, rtppay, self.udpsink)

        # Decoder chain: UDP → RTP → Opus → tee
        if not is_listen_only:
            self._link_many_or_raise("decoder", self.udpsrc, rtp_capsfilter, self.jitter, rtpdepay, opusdec, audconv2, audres2, caps2, playout_tee)
            self._link_many_or_raise("playout", playout_q, sink)

        # VAD branch: tee → queue → convert → resample → caps → appsink
        # This works in both modes since it comes from capture path
        self._link_tee_src_to("capture→vad", capture_tee, vad_q)
        self._link_many_or_raise("vad", vad_q, vad_conv, vad_res, vad_caps, self.vad_sink)
        
        # DFN branch: tee → queue
        self._link_tee_src_to("capture→dfn", capture_tee, dfn_q)
        
        if not is_listen_only:
            self._link_many_or_raise("decoder", self.udpsrc, rtp_capsfilter, self.jitter, rtpdepay, opusdec, audconv2, audres2, caps2, playout_tee)
            self._link_many_or_raise("playout", playout_q, sink)
            self._link_tee_src_to("playout→sink", playout_tee, playout_q)
            self._link_tee_src_to("playout→render", playout_tee, render_q)
            
            if self.aec:
                render_pad = self.aec.get_request_pad("render_sink")
                if render_pad:
                    render_src = render_q.get_static_pad("src")
                    self._pad_link_or_raise("render→aec", render_src, render_pad)
                else:
                    self.logger.warning("AEC render pad not available")

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
        if ret[0] != Gst.StateChangeReturn.SUCCESS:
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
        else:
            self.logger.info("Pipeline reached PLAYING state successfully")
        
        # Verify VAD sink state
        vad_sink_state = self.vad_sink.get_state(timeout=1 * Gst.SECOND)
        self.logger.info("VAD sink state after pipeline start: %s -> %s", 
                        vad_sink_state[1].value_nick, vad_sink_state[2].value_nick)
        
        self.clock = self.pipeline.get_clock()
        self.base_time = self.pipeline.get_base_time()
        
        # Export pipeline graph for debugging
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, "tchat_pipeline")
        self.logger.info("Pipeline graph exported to GST_DEBUG_DUMP_DOT_DIR (if set)")
        
        self.vad.start()
        self.logger.info("Pipeline started (local_port=%d, remote=%s:%s)", 
                        local_port, remote_ip or "none", remote_port or "none")
        self.logger.info("Audio devices: input=%s, output=%s", 
                        input_device or "default", output_device or "default")

    def stop(self):
        if not self.pipeline:
            return
        self.vad.stop()
        
        if self.bus:
            self.bus.remove_signal_watch()
            self.bus = None
        
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.queues = {}
        self.vad_sink = None
        self.logger.info("Pipeline stopped")

    def set_remote(self, ip, port):
        if self.udpsink:
            self.udpsink.set_property("host", ip)
            self.udpsink.set_property("port", int(port))

    def poll_metrics(self):
        for name, queue in self.queues.items():
            try:
                depth = queue.get_property("current-level-buffers")
                self.metrics.update_queue_depth(name, depth)
            except Exception:
                continue
        if self.jitter:
            try:
                stats = self.jitter.get_property("stats")
                if stats and stats.has_field("packets-in-queue"):
                    depth = stats.get_value("packets-in-queue")
                    self.metrics.update_jitter_depth(int(depth))
                    self._adapt_jitter(int(depth))
            except Exception:
                pass

    def _on_vad_sample(self, sink):
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.OK
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
        elif t == Gst.MessageType.ELEMENT:
            struct = message.get_structure()
            if struct and struct.get_name() == "dfn-stats":
                p50 = struct.get_value("p50_ms")
                p95 = struct.get_value("p95_ms")
                bypass = struct.get_value("bypass_count")
                self.metrics.update_dfn_stats(p50, p95, bypass)

    def _on_send_probe(self, pad, info):
        buf = info.get_buffer()
        if not buf or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        if not self.clock or self.base_time is None:
            return Gst.PadProbeReturn.OK
        now = self.clock.get_time() - self.base_time
        latency_ns = now - buf.pts
        latency_ms = latency_ns / Gst.MSECOND
        self.metrics.update_mic_send_latency(latency_ms)
        return Gst.PadProbeReturn.OK

    def _make_queue(self, name):
        queue = Gst.ElementFactory.make("queue", name)
        queue.set_property("max-size-buffers", 3)
        queue.set_property("max-size-time", 0)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("leaky", 2)
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

    def _adapt_jitter(self, packets_in_queue):
        if not self.jitter:
            return
        if packets_in_queue > 6 and self.jitter_latency_ms < 60:
            self.jitter_latency_ms = min(60, self.jitter_latency_ms + 5)
            self.jitter.set_property("latency", self.jitter_latency_ms)
        elif packets_in_queue < 2 and self.jitter_latency_ms > 20:
            self.jitter_latency_ms = max(20, self.jitter_latency_ms - 5)
            self.jitter.set_property("latency", self.jitter_latency_ms)

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

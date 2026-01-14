import logging
import os
import threading
from collections import deque

import numpy as np

try:
    import onnxruntime as ort
except Exception:
    ort = None

from .utils import FrameRingBuffer


class VADWorker(threading.Thread):
    """
    Silero VAD v4 ONNX implementation following official specifications:
    - Input: [1, 576] = 64 context samples + 512 audio samples (at 16kHz)
    - State: [2, 1, 128] recurrent hidden state
    - SR: scalar int64 sample rate (16000)
    - Output: probability + updated state
    """
    
    SAMPLE_RATE = 16000
    WINDOW_SIZE = 512
    CONTEXT_SIZE = 64
    INPUT_SIZE = WINDOW_SIZE + CONTEXT_SIZE
    STATE_SHAPE = (2, 1, 128)
    
    def __init__(self, ring: FrameRingBuffer, metrics, model_path, stop_event):
        super().__init__(daemon=True)
        self.ring = ring
        self.metrics = metrics
        self.model_path = model_path
        self.stop_event = stop_event
        self.logger = logging.getLogger("VAD")
        self.session = None
        self.input_buf = np.zeros(self.INPUT_SIZE, dtype=np.float32)
        self.input_view = self.input_buf.reshape(1, -1)
        self.feed = None

        def _env_float(name, default):
            try:
                return float(os.getenv(name, default))
            except ValueError:
                return default

        self.prob_on = _env_float("VAD_PROB_ON", 0.5)
        self.prob_off = _env_float("VAD_PROB_OFF", 0.35)
        self.energy_on_db = _env_float("VAD_ENERGY_DB_ON", -40.0)
        self.energy_off_db = _env_float("VAD_ENERGY_DB_OFF", -50.0)
        self.use_energy_fallback = os.getenv("VAD_ENERGY_FALLBACK", "1") != "0"
        
        self._reset_state()
        
        self.buffer_16k = deque()
        self.buffer_samples = 0
        self.speaking = False
        self.above_ms = 0.0
        self.below_ms = 0.0
        
    def _reset_state(self):
        self.state = np.zeros(self.STATE_SHAPE, dtype=np.float32)
        self.context = np.zeros(self.CONTEXT_SIZE, dtype=np.float32)
        self.sr = np.array(self.SAMPLE_RATE, dtype=np.int64)
        if self.input_buf is not None:
            self.input_buf.fill(0.0)

    def reset_runtime(self):
        self._reset_state()
        self.buffer_16k.clear()
        self.buffer_samples = 0
        self.speaking = False
        self.above_ms = 0.0
        self.below_ms = 0.0

    def load_model(self):
        if self.session is not None:
            return True
        if ort is None:
            self.logger.warning("onnxruntime not available; VAD disabled")
            return False
        if not os.path.exists(self.model_path):
            self.logger.warning("VAD model not found: %s", self.model_path)
            return False
        if os.path.getsize(self.model_path) < 1024:
            self.logger.warning("VAD model placeholder detected: %s", self.model_path)
            return False
        try:
            sess = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
            inputs = sess.get_inputs()
            outputs = sess.get_outputs()
            
            input_info = [(inp.name, inp.shape, inp.type) for inp in inputs]
            output_info = [(out.name, out.shape, out.type) for out in outputs]
            self.logger.info("VAD model inputs: %s", input_info)
            self.logger.info("VAD model outputs: %s", output_info)
            
            self.session = sess
            self.logger.info("VAD model loaded: %s", self.model_path)
            self.logger.info("Config: window=%d, context=%d, total_input=%d @ %dHz",
                           self.WINDOW_SIZE, self.CONTEXT_SIZE, self.INPUT_SIZE, self.SAMPLE_RATE)
            self.feed = {
                'input': self.input_view,
                'state': self.state,
                'sr': self.sr
            }
            return True
        except Exception as exc:
            self.logger.exception("Failed to load VAD model: %s", exc)
            return False

    def run(self):
        model_ok = self.load_model()
        if not model_ok:
            self.logger.warning("VAD running in energy-only mode")

        self.logger.info("VAD worker started")
        frame_ms = self.WINDOW_SIZE / self.SAMPLE_RATE * 1000.0
        frame_count = 0
        error_count = 0
        max_errors = 5
        
        self._reset_state()
        self.buffer_16k.clear()
        self.buffer_samples = 0

        while not self.stop_event.is_set():
            frame = self.ring.pop(timeout=0.1)
            if frame is None:
                continue
                
            pcm_s16 = np.frombuffer(frame, dtype=np.int16)
            if pcm_s16.size == 0:
                continue
                
            pcm_16k = pcm_s16.astype(np.float32) / 32768.0
            self.buffer_16k.append(pcm_16k)
            self.buffer_samples += pcm_16k.size
            
            while self.buffer_samples >= self.WINDOW_SIZE:
                chunk = self._pop_samples(self.WINDOW_SIZE)
                
                rms = float(np.sqrt(np.mean(chunk * chunk)))
                energy_db = 20.0 * np.log10(max(rms, 1e-12))
                denom = max(1e-6, self.energy_on_db - self.energy_off_db)
                energy_prob = (energy_db - self.energy_off_db) / denom
                energy_prob = float(np.clip(energy_prob, 0.0, 1.0))

                self.input_buf[:self.CONTEXT_SIZE] = self.context
                self.input_buf[self.CONTEXT_SIZE:] = chunk
                prob_value = energy_prob

                if self.session:
                    try:
                        if self.feed is None:
                            self.feed = {'input': self.input_view, 'state': self.state, 'sr': self.sr}
                        else:
                            self.feed['state'] = self.state
                        res = self.session.run(None, self.feed)
                        
                        prob = res[0]
                        if len(res) > 1:
                            self.state = res[1]
                            
                        prob_value = float(np.squeeze(prob))
                        if self.use_energy_fallback:
                            prob_value = max(prob_value, energy_prob)
                        error_count = 0
                    except Exception:
                        error_count += 1
                        if error_count <= max_errors:
                            self.logger.exception("VAD inference failed (input shape: %s)", self.input_view.shape)
                        elif error_count == max_errors + 1:
                            self.logger.error("VAD inference errors exceeded limit, suppressing further logs")
                        if not self.use_energy_fallback:
                            prob_value = 0.0

                self.context[:] = chunk[-self.CONTEXT_SIZE:]
                frame_count += 1
                if frame_count % 30 == 0:
                    if self.session:
                        self.logger.debug("VAD prob=%.3f speaking=%s", prob_value, self.speaking)
                    else:
                        self.logger.debug("VAD energy=%.1f dB speaking=%s", energy_db, self.speaking)

                self._update_speaking(prob_value, energy_db, frame_ms)
                self.metrics.update_vad(prob_value, self.speaking, energy_db)

    def _pop_samples(self, count):
        out = np.empty(count, dtype=np.float32)
        filled = 0
        while filled < count and self.buffer_16k:
            buf = self.buffer_16k[0]
            take = min(count - filled, buf.size)
            out[filled:filled + take] = buf[:take]
            if take == buf.size:
                self.buffer_16k.popleft()
            else:
                self.buffer_16k[0] = buf[take:]
            filled += take
            self.buffer_samples -= take
        return out

    def _update_speaking(self, prob_value, energy_db, frame_ms):
        if self.use_energy_fallback:
            above = prob_value > self.prob_on or energy_db > self.energy_on_db
            below = prob_value < self.prob_off and energy_db < self.energy_off_db
        else:
            above = prob_value > self.prob_on
            below = prob_value < self.prob_off

        if above:
            self.above_ms += frame_ms
            self.below_ms = 0.0
        elif below:
            self.below_ms += frame_ms
            self.above_ms = 0.0

        if not self.speaking and self.above_ms >= 30.0:
            self.speaking = True
            self.logger.info("Speech started (prob=%.2f, energy=%.1f dB)", prob_value, energy_db)
        elif self.speaking and self.below_ms >= 200.0:
            self.speaking = False
            self.logger.info("Speech stopped (prob=%.2f, energy=%.1f dB)", prob_value, energy_db)


class VADManager:
    def __init__(self, metrics, model_path, ring_frames=120):
        self.metrics = metrics
        self.model_path = model_path
        self.ring = FrameRingBuffer(max_frames=ring_frames)
        self.stop_event = threading.Event()
        self.worker = VADWorker(self.ring, metrics, model_path, self.stop_event)
        self.logger = logging.getLogger("VAD")
        self.frame_count = 0
        self._preloaded = False

    def push_frame(self, frame_bytes):
        self.frame_count += 1
        if self.frame_count == 1:
            self.logger.info("First audio frame received, VAD processing started")
        elif self.frame_count % 100 == 0:
            self.logger.debug("Received %d audio frames", self.frame_count)
        self.ring.push(frame_bytes)

    def start(self):
        if self.worker.is_alive():
            self.logger.warning("VAD worker already running")
            return
        self.stop_event.clear()
        if self._preloaded:
            self._preloaded = False
            self.worker.reset_runtime()
        else:
            self.worker = VADWorker(self.ring, self.metrics, self.model_path, self.stop_event)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=2.0)

    def preload(self):
        if self._preloaded:
            return
        if self.worker.is_alive():
            return
        try:
            self.worker = VADWorker(self.ring, self.metrics, self.model_path, self.stop_event)
            ok = self.worker.load_model()
            if ok:
                self._preloaded = True
                self.logger.info("VAD model preloaded")
        except Exception as exc:
            self.logger.warning("VAD preload failed: %s", exc)

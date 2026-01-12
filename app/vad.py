import logging
import os
import threading

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
        
        self._reset_state()
        
        self.buffer_16k = np.zeros(0, dtype=np.float32)
        self.speaking = False
        self.above_ms = 0.0
        self.below_ms = 0.0
        
    def _reset_state(self):
        self.state = np.zeros(self.STATE_SHAPE, dtype=np.float32)
        self.context = np.zeros(self.CONTEXT_SIZE, dtype=np.float32)
        self.sr = np.array(self.SAMPLE_RATE, dtype=np.int64)

    def load_model(self):
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
            return True
        except Exception as exc:
            self.logger.exception("Failed to load VAD model: %s", exc)
            return False

    def run(self):
        if not self.load_model():
            self.logger.warning("VAD disabled - model not loaded")
            return
            
        self.logger.info("VAD worker started")
        frame_ms = self.WINDOW_SIZE / self.SAMPLE_RATE * 1000.0
        frame_count = 0
        error_count = 0
        max_errors = 5
        
        self._reset_state()

        while not self.stop_event.is_set():
            frame = self.ring.pop(timeout=0.1)
            if frame is None:
                continue
                
            pcm_s16 = np.frombuffer(frame, dtype=np.int16)
            if pcm_s16.size == 0:
                continue
                
            pcm_16k = pcm_s16.astype(np.float32) / 32768.0
            self.buffer_16k = np.concatenate([self.buffer_16k, pcm_16k])
            
            while self.buffer_16k.size >= self.WINDOW_SIZE:
                chunk = self.buffer_16k[:self.WINDOW_SIZE]
                self.buffer_16k = self.buffer_16k[self.WINDOW_SIZE:]
                
                input_data = np.concatenate([self.context, chunk])
                inp = input_data.reshape(1, -1).astype(np.float32)
                
                try:
                    if not self.session:
                        break
                    
                    feed = {
                        'input': inp,
                        'state': self.state,
                        'sr': self.sr
                    }
                    
                    res = self.session.run(None, feed)
                    
                    prob = res[0]
                    if len(res) > 1:
                        self.state = res[1]
                    
                    self.context = chunk[-self.CONTEXT_SIZE:]
                        
                    prob_value = float(np.squeeze(prob))
                    error_count = 0
                    
                except Exception as exc:
                    error_count += 1
                    if error_count <= max_errors:
                        self.logger.exception("VAD inference failed (input shape: %s)", inp.shape)
                    elif error_count == max_errors + 1:
                        self.logger.error("VAD inference errors exceeded limit, suppressing further logs")
                    prob_value = 0.0
                    continue
                    
                frame_count += 1
                if frame_count % 30 == 0:
                    self.logger.debug("VAD prob=%.3f speaking=%s", prob_value, self.speaking)
                    
                if prob_value > 0.6:
                    self.above_ms += frame_ms
                    self.below_ms = 0.0
                elif prob_value < 0.4:
                    self.below_ms += frame_ms
                    self.above_ms = 0.0
                    
                if not self.speaking and self.above_ms >= 30.0:
                    self.speaking = True
                    self.logger.info("Speech started (prob=%.2f)", prob_value)
                elif self.speaking and self.below_ms >= 200.0:
                    self.speaking = False
                    self.logger.info("Speech stopped (prob=%.2f)", prob_value)
                    
                self.metrics.update_vad(prob_value, self.speaking)


class VADManager:
    def __init__(self, metrics, model_path, ring_frames=120):
        self.metrics = metrics
        self.model_path = model_path
        self.ring = FrameRingBuffer(max_frames=ring_frames)
        self.stop_event = threading.Event()
        self.worker = VADWorker(self.ring, metrics, model_path, self.stop_event)
        self.logger = logging.getLogger("VAD")
        self.frame_count = 0

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
        self.worker = VADWorker(self.ring, self.metrics, self.model_path, self.stop_event)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=2.0)

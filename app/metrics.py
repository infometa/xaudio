import threading
import time


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "dfn_p50_ms": None,
            "dfn_p95_ms": None,
            "dfn_bypass": 0,
            "dfn_auto_mix": None,
            "dfn_auto_bypass": False,
            "queue_depths": {},
            "queue_overruns": {},
            "jitter_depth": None,
            "jitter_kind": None,
            "mic_send_latency_ms": None,
            "vad_prob": 0.0,
            "vad_speaking": False,
            "vad_energy_db": None,
            "input_sample_rate": None,
            "target_sample_rate": None,
            "last_update": time.time(),
        }

    def update_dfn_stats(self, p50_ms, p95_ms, bypass_count, auto_mix=None, auto_bypass=None):
        with self._lock:
            self._data["dfn_p50_ms"] = p50_ms
            self._data["dfn_p95_ms"] = p95_ms
            self._data["dfn_bypass"] = bypass_count
            if auto_mix is not None:
                self._data["dfn_auto_mix"] = auto_mix
            if auto_bypass is not None:
                self._data["dfn_auto_bypass"] = auto_bypass
            self._data["last_update"] = time.time()

    def update_queue_depth(self, name, depth):
        with self._lock:
            self._data["queue_depths"][name] = depth
            self._data["last_update"] = time.time()

    def update_queue_overrun(self, name, count):
        with self._lock:
            self._data["queue_overruns"][name] = count
            self._data["last_update"] = time.time()

    def update_jitter_depth(self, depth, kind=None):
        with self._lock:
            self._data["jitter_depth"] = depth
            if kind is not None:
                self._data["jitter_kind"] = kind
            self._data["last_update"] = time.time()

    def update_mic_send_latency(self, latency_ms):
        with self._lock:
            self._data["mic_send_latency_ms"] = latency_ms
            self._data["last_update"] = time.time()

    def update_vad(self, prob, speaking, energy_db=None):
        with self._lock:
            self._data["vad_prob"] = prob
            self._data["vad_speaking"] = speaking
            if energy_db is not None:
                self._data["vad_energy_db"] = energy_db
            self._data["last_update"] = time.time()

    def update_sample_rates(self, input_rate=None, target_rate=None):
        with self._lock:
            if input_rate is not None:
                self._data["input_sample_rate"] = input_rate
            if target_rate is not None:
                self._data["target_sample_rate"] = target_rate
            self._data["last_update"] = time.time()

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def clear_runtime(self):
        with self._lock:
            self._data["queue_depths"] = {}
            self._data["queue_overruns"] = {}
            self._data["jitter_depth"] = None
            self._data["jitter_kind"] = None
            self._data["mic_send_latency_ms"] = None
            self._data["vad_prob"] = 0.0
            self._data["vad_speaking"] = False
            self._data["vad_energy_db"] = None
            self._data["dfn_auto_mix"] = None
            self._data["dfn_auto_bypass"] = False
            self._data["input_sample_rate"] = None
            self._data["target_sample_rate"] = None
            self._data["last_update"] = time.time()

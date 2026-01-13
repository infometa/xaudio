import threading
import time


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "dfn_p50_ms": None,
            "dfn_p95_ms": None,
            "dfn_bypass": 0,
            "queue_depths": {},
            "jitter_depth": None,
            "jitter_kind": None,
            "mic_send_latency_ms": None,
            "vad_prob": 0.0,
            "vad_speaking": False,
            "vad_energy_db": None,
            "last_update": time.time(),
        }

    def update_dfn_stats(self, p50_ms, p95_ms, bypass_count):
        with self._lock:
            self._data["dfn_p50_ms"] = p50_ms
            self._data["dfn_p95_ms"] = p95_ms
            self._data["dfn_bypass"] = bypass_count
            self._data["last_update"] = time.time()

    def update_queue_depth(self, name, depth):
        with self._lock:
            self._data["queue_depths"][name] = depth
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

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def clear_runtime(self):
        with self._lock:
            self._data["queue_depths"] = {}
            self._data["jitter_depth"] = None
            self._data["jitter_kind"] = None
            self._data["mic_send_latency_ms"] = None
            self._data["vad_prob"] = 0.0
            self._data["vad_speaking"] = False
            self._data["vad_energy_db"] = None
            self._data["last_update"] = time.time()

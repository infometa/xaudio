import logging
import threading
from collections import deque


class FrameRingBuffer:
    """Thread-safe ring buffer for fixed-size audio frames."""

    def __init__(self, max_frames):
        self._frames = deque(maxlen=max_frames)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._drop_count = 0
        self._logger = logging.getLogger("RingBuffer")

    def push(self, frame_bytes):
        with self._lock:
            if len(self._frames) == self._frames.maxlen:
                self._frames.popleft()
                self._drop_count += 1
                if self._drop_count == 1 or self._drop_count % 50 == 0:
                    self._logger.warning("VAD ring buffer overflow, dropped %d frames", self._drop_count)
            self._frames.append(frame_bytes)
            self._cond.notify()

    def pop(self, timeout=None):
        with self._cond:
            if not self._frames:
                self._cond.wait(timeout)
            if not self._frames:
                return None
            return self._frames.popleft()

    def size(self):
        with self._lock:
            return len(self._frames)

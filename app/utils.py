import threading
from collections import deque


class FrameRingBuffer:
    """Thread-safe ring buffer for fixed-size audio frames."""

    def __init__(self, max_frames):
        self._frames = deque(maxlen=max_frames)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def push(self, frame_bytes):
        with self._lock:
            if len(self._frames) == self._frames.maxlen:
                self._frames.popleft()
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

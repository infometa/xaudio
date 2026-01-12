import os
import sys
import signal

# Set GST_PLUGIN_PATH before GStreamer initialization
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CUSTOM_PLUGINS_DIR = os.path.join(ROOT_DIR, "native", "build", "gst-plugins")
HOMEBREW_PLUGINS_DIR = "/opt/homebrew/lib/gstreamer-1.0"

# Set plugin path: custom plugins first, then homebrew
os.environ["GST_PLUGIN_PATH"] = f"{CUSTOM_PLUGINS_DIR}:{HOMEBREW_PLUGINS_DIR}"

# CRITICAL: Initialize GStreamer BEFORE importing PySide6
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

# Initialize GStreamer first
Gst.init(['--gst-disable-registry-fork'])

# Now import PySide6
from PySide6 import QtWidgets, QtCore

from .logging_config import setup_logging
from .metrics import Metrics
from .media import MediaEngine
from .ui import MainWindow
from .vad import VADManager


def main():
    setup_logging()

    app = QtWidgets.QApplication(sys.argv)

    metrics = Metrics()
    model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "silero_vad.onnx"))
    vad = VADManager(metrics, model_path)
    media = MediaEngine(metrics, vad)

    from .signaling import Signaling

    signaling = Signaling()
    window = MainWindow(media, signaling, metrics)
    window.resize(620, 560)
    window.show()

    def handle_sigint(signum, frame):
        print("\nReceived Ctrl+C, shutting down gracefully...")
        QtWidgets.QApplication.quit()

    signal.signal(signal.SIGINT, handle_sigint)

    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt caught, exiting...")
        sys.exit(0)


if __name__ == "__main__":
    main()

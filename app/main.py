import argparse
import os
import sys
import signal


def parse_args():
    parser = argparse.ArgumentParser(description="TChat P2P Voice Client")
    parser.add_argument("--port", type=int, default=5004,
                        help="Local RTP port (default: 5004)")
    parser.add_argument("--auto-listen", action="store_true",
                        help="Automatically start listening after launch")
    parser.add_argument("--auto-call", type=str, metavar="IP:PORT",
                        help="Automatically call remote after launch (e.g., 127.0.0.1:5004)")
    return parser.parse_args()


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CUSTOM_PLUGINS_DIR = os.path.join(ROOT_DIR, "native", "build", "gst-plugins")
HOMEBREW_PLUGINS_DIR = "/opt/homebrew/lib/gstreamer-1.0"

os.environ["GST_PLUGIN_PATH"] = f"{CUSTOM_PLUGINS_DIR}:{HOMEBREW_PLUGINS_DIR}"

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(['--gst-disable-registry-fork'])

from PySide6 import QtWidgets, QtCore

from .logging_config import setup_logging
from .metrics import Metrics
from .media import MediaEngine
from .ui import MainWindow
from .vad import VADManager


def main():
    args = parse_args()
    env_local_port = os.getenv("TCHAT_DEFAULT_LOCAL_PORT")
    if env_local_port:
        try:
            env_port = int(env_local_port)
        except ValueError:
            env_port = None
        if env_port and args.port == 5004:
            args.port = env_port
    setup_logging()

    app = QtWidgets.QApplication(sys.argv)

    metrics = Metrics()
    model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "silero_vad.onnx"))
    vad = VADManager(metrics, model_path)
    media = MediaEngine(metrics, vad)
    vad.preload()
    media.prewarm()

    from .signaling import Signaling

    signaling = Signaling()
    
    window = MainWindow(
        media, signaling, metrics,
        initial_port=args.port,
        auto_listen=args.auto_listen,
        auto_call=args.auto_call
    )
    window.resize(960, 560)
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

#!/usr/bin/env bash
set -euo pipefail

# TChat Client - Uses port 5006, connects to localhost:5004
# RTP: 5006, Signaling: 5007

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source /opt/anaconda3/etc/profile.d/conda.sh
conda activate tchat

export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig"
export PATH="/opt/homebrew/bin:$PATH"
export GI_TYPELIB_PATH="/opt/homebrew/lib/girepository-1.0"
export GST_PLUGIN_PATH="$ROOT_DIR/native/build/gst-plugins:/opt/homebrew/lib/gstreamer-1.0"
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:$ROOT_DIR/onnxruntime-osx-arm64-1.23.2/lib"
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:$ROOT_DIR/onnxruntime-osx-arm64-1.23.2/lib"
export ONNXRUNTIME_ROOT="$ROOT_DIR/onnxruntime-osx-arm64-1.23.2"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

rm -rf ~/.cache/gstreamer-1.0/

echo "========================================"
echo "TChat Client (Port 5006 -> 127.0.0.1:5004)"
echo "========================================"
echo "Local RTP Port: 5006"
echo "Local Signaling Port: 5007"
echo "Connecting to: 127.0.0.1:5004"
echo "========================================"
echo ""

cd "$ROOT_DIR"
python -m app.main --port 5006 --auto-call 127.0.0.1:5004 2> >(grep -v "Class ResultReceiver\|Class GtkApplicationQuartzDelegate\|Class GNSMenuItem\|Class GNSMenu\|Class FilterComboBox\|Class gdkCoreCursor" >&2)

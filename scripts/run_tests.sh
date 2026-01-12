#!/usr/bin/env bash
set -euo pipefail

# TChat Complete Test Suite
# This script runs all tests with proper environment setup

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "TChat Complete Test Suite"
echo "========================================"
echo ""

# Activate conda environment
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate tchat

# Setup environment variables for GStreamer/GLib (Homebrew)
export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig"
export PATH="/opt/homebrew/bin:$PATH"
export GI_TYPELIB_PATH="/opt/homebrew/lib/girepository-1.0"
export GST_PLUGIN_PATH="$ROOT_DIR/native/build/gst-plugins:/opt/homebrew/lib/gstreamer-1.0"

# CRITICAL: Set library paths for GLib/GStreamer
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:$ROOT_DIR/onnxruntime-osx-arm64-1.23.2/lib"
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:$ROOT_DIR/onnxruntime-osx-arm64-1.23.2/lib"

# ONNX Runtime
export ONNXRUNTIME_ROOT="$ROOT_DIR/onnxruntime-osx-arm64-1.23.2"

# Clear GStreamer cache to avoid stale issues
rm -rf ~/.cache/gstreamer-1.0/

# Track results
PASSED=0
FAILED=0

run_test() {
    local test_name="$1"
    local test_cmd="$2"
    
    printf "%-40s" "Testing: $test_name..."
    
    if eval "$test_cmd" > /tmp/tchat_test_output.txt 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        ((PASSED++))
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        echo "  Output:"
        head -20 /tmp/tchat_test_output.txt | sed 's/^/    /'
        ((FAILED++))
        return 1
    fi
}

echo "Environment:"
echo "  Python: $(which python)"
echo "  GST_PLUGIN_PATH: $GST_PLUGIN_PATH"
echo ""

# Test 1: Python basic imports
echo "--- Phase 1: Basic Imports ---"
run_test "Python environment" "python -c 'import sys; print(sys.version)'"
run_test "NumPy" "python -c 'import numpy as np; print(np.__version__)'"
run_test "ONNX Runtime" "python -c 'import onnxruntime as ort; print(ort.__version__)'"
run_test "PySide6" "python -c 'from PySide6 import QtCore; print(QtCore.__version__)'"
run_test "sounddevice" "python -c 'import sounddevice as sd; print(sd.__version__)'"

# Test 2: GStreamer
echo ""
echo "--- Phase 2: GStreamer ---"
run_test "GStreamer import" "python -c 'import gi; gi.require_version(\"Gst\", \"1.0\"); from gi.repository import Gst; Gst.init(None); print(Gst.version_string())'"

# Test 3: App modules
echo ""
echo "--- Phase 3: Application Modules ---"
run_test "app.metrics" "python -c 'from app.metrics import Metrics; m = Metrics(); print(\"OK\")'"
run_test "app.utils" "python -c 'from app.utils import FrameRingBuffer; r = FrameRingBuffer(10); print(\"OK\")'"
run_test "app.logging_config" "python -c 'from app.logging_config import setup_logging; print(\"OK\")'"
run_test "app.signaling" "python -c 'from app.signaling import Signaling; s = Signaling(); print(\"OK\")'"

# Test 4: VAD model
echo ""
echo "--- Phase 4: VAD Model ---"
run_test "VAD model inference" "python test_vad_standalone.py"

# Test 5: GStreamer plugins
echo ""
echo "--- Phase 5: GStreamer Plugins ---"

# Suppress objc warnings for cleaner output
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

run_test "deepfilternet plugin" "python -c \"
import gi; gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
elem = Gst.ElementFactory.make('deepfilternet', None)
assert elem is not None, 'Plugin not found'
print('OK')
\" 2>/dev/null"

run_test "webrtcaec3 plugin" "python -c \"
import gi; gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
elem = Gst.ElementFactory.make('webrtcaec3', None)
assert elem is not None, 'Plugin not found'
print('OK')
\" 2>/dev/null"

run_test "opusenc plugin" "python -c \"
import gi; gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
elem = Gst.ElementFactory.make('opusenc', None)
assert elem is not None, 'Plugin not found'
print('OK')
\" 2>/dev/null"

run_test "osxaudiosrc plugin" "python -c \"
import gi; gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
elem = Gst.ElementFactory.make('osxaudiosrc', None)
assert elem is not None, 'Plugin not found'
print('OK')
\" 2>/dev/null"

# Test 6: MediaEngine creation (without starting pipeline)
echo ""
echo "--- Phase 6: MediaEngine ---"
run_test "MediaEngine creation" "python -c \"
import os
os.environ['GST_PLUGIN_PATH'] = '$(pwd)/native/build/gst-plugins:/opt/homebrew/lib/gstreamer-1.0'
import gi; gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(['--gst-disable-registry-fork'])
from app.metrics import Metrics
from app.vad import VADManager
from app.media import MediaEngine
m = Metrics()
vad = VADManager(m, 'models/silero_vad.onnx')
engine = MediaEngine(m, vad)
sources, sinks = engine.list_devices()
print(f'Found {len(sources)} sources, {len(sinks)} sinks')
\" 2>/dev/null"

# Test 7: Full functionality test
echo ""
echo "--- Phase 7: Full Functionality Test ---"
run_test "Comprehensive test" "python test_functionality.py 2>/dev/null"

# Summary
echo ""
echo "========================================"
echo "Test Summary"
echo "========================================"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    echo ""
    echo "You can now run the application:"
    echo "  ./scripts/run_dev.sh"
    exit 0
else
    echo -e "${RED}Some tests failed. Please review the errors above.${NC}"
    exit 1
fi

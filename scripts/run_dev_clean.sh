#!/usr/bin/env bash
set -euo pipefail

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
export GST_DEBUG=3
export GST_DEBUG_NO_COLOR=1

rm -rf ~/.cache/gstreamer-1.0/

echo "========================================"
echo "TChat 开发环境 (Homebrew GStreamer)"
echo "========================================"
echo "项目根目录: $ROOT_DIR"
echo "GST_PLUGIN_PATH: $GST_PLUGIN_PATH"
echo "ONNXRUNTIME_ROOT: $ONNXRUNTIME_ROOT"
echo "GI_TYPELIB_PATH: $GI_TYPELIB_PATH"
echo "========================================"
echo ""

if [ ! -f "$ROOT_DIR/native/build/gst-plugins/libgstwebrtcaec3.dylib" ]; then
    echo "警告: WebRTC AEC3 插件未找到"
    echo "请先运行: cmake --build native/build"
    echo ""
else
    echo "✓ WebRTC AEC3 插件已找到"
fi

if [ ! -f "$ROOT_DIR/native/build/gst-plugins/libgstdeepfilternet.dylib" ]; then
    echo "警告: DeepFilterNet 插件未找到"
    echo "请先运行: cmake --build native/build"
    echo ""
else
    echo "✓ DeepFilterNet 插件已找到"
fi

echo ""

cd "$ROOT_DIR"

/opt/anaconda3/envs/tchat/bin/python -m app.main "$@" 2> >(
    grep -v "Class ResultReceiver is implemented in both" |
    grep -v "Class GtkApplicationQuartzDelegate is implemented in both" |
    grep -v "Class GNSMenuItem is implemented in both" |
    grep -v "Class GNSMenu is implemented in both" |
    grep -v "Class FilterComboBox is implemented in both" |
    grep -v "Class gdkCoreCursor is implemented in both" |
    grep -v "This may cause spurious casting failures" >&2
)

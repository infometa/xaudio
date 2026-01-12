#!/usr/bin/env bash
set -euo pipefail

# TChat Development Runner for macOS with Homebrew GStreamer
# 使用 Homebrew GStreamer 而非 conda 版本

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 激活 conda 环境
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate tchat

# Homebrew GStreamer 环境 (替代 Framework 版本)
export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig"
export PATH="/opt/homebrew/bin:$PATH"
export GI_TYPELIB_PATH="/opt/homebrew/lib/girepository-1.0"
export GST_PLUGIN_PATH="$ROOT_DIR/native/build/gst-plugins:/opt/homebrew/lib/gstreamer-1.0"

# 动态库路径 - Homebrew 优先（关键：解决 Conda/Homebrew GStreamer 冲突）
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:$ROOT_DIR/onnxruntime-osx-arm64-1.23.2/lib"
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:$ROOT_DIR/onnxruntime-osx-arm64-1.23.2/lib"

# ONNX Runtime (用于 DeepFilterNet 和 VAD)
export ONNXRUNTIME_ROOT="$ROOT_DIR/onnxruntime-osx-arm64-1.23.2"

# 清除 GStreamer 缓存
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

# 检查插件是否存在
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

# 运行应用（过滤 GTK 类冲突警告）
cd "$ROOT_DIR"

# 方法1: 使用环境变量抑制 Objective-C 类冲突警告
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

# 方法2: 过滤stderr中的GTK警告，保留真正的错误
python -m app.main "$@" 2> >(grep -v "Class ResultReceiver\|Class GtkApplicationQuartzDelegate\|Class GNSMenuItem\|Class GNSMenu\|Class FilterComboBox\|Class gdkCoreCursor" >&2)

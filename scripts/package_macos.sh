#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT_DIR/native/build/gst-plugins"
GST_ROOT="${GST_ROOT:-/Library/Frameworks/GStreamer.framework}"
ONNXRUNTIME_ROOT="${ONNXRUNTIME_ROOT:-}"
PYTHON_BIN="${PYTHON:-python3}"

${PYTHON_BIN} -m pip install --upgrade pyinstaller

${PYTHON_BIN} -m PyInstaller \
  --name TChat \
  --windowed \
  --add-data "$ROOT_DIR/models:models" \
  --add-binary "$PLUGIN_DIR/*.dylib:gst-plugins" \
  --paths "$ROOT_DIR" \
  "$ROOT_DIR/app/main.py"

APP_DIR="$ROOT_DIR/dist/TChat.app"
FRAMEWORKS_DIR="$APP_DIR/Contents/Frameworks"
MACOS_DIR="$APP_DIR/Contents/MacOS"

if [ ! -d "$APP_DIR" ]; then
  echo "App bundle not found: $APP_DIR"
  exit 1
fi

mkdir -p "$FRAMEWORKS_DIR"

if [ -d "$GST_ROOT" ]; then
  echo "Copying GStreamer.framework..."
  rsync -a --delete "$GST_ROOT" "$FRAMEWORKS_DIR/"
else
  echo "Warning: GStreamer.framework not found at $GST_ROOT"
fi

if [ -d "$PLUGIN_DIR" ]; then
  mkdir -p "$FRAMEWORKS_DIR/gst-plugins"
  rsync -a "$PLUGIN_DIR/" "$FRAMEWORKS_DIR/gst-plugins/"
else
  echo "Warning: native plugins not found at $PLUGIN_DIR"
fi

if [ -n "$ONNXRUNTIME_ROOT" ]; then
  ONNX_DYLIB="$ONNXRUNTIME_ROOT/libonnxruntime.dylib"
  if [ ! -f "$ONNX_DYLIB" ]; then
    ONNX_DYLIB="$ONNXRUNTIME_ROOT/lib/libonnxruntime.dylib"
  fi
  if [ -f "$ONNX_DYLIB" ]; then
    echo "Copying ONNX Runtime dylib..."
    cp -f "$ONNX_DYLIB" "$FRAMEWORKS_DIR/"
  else
    echo "Warning: onnxruntime dylib not found under $ONNXRUNTIME_ROOT"
  fi
fi

# Avoid shipping libiconv from Python envs; it lacks iconv symbols and breaks gi/glib.
if [ -f "$FRAMEWORKS_DIR/libiconv.2.dylib" ]; then
  echo "Removing bundled libiconv to use system libiconv..."
  rm -f "$FRAMEWORKS_DIR/libiconv.2.dylib"
fi

if [ -f "$MACOS_DIR/TChat" ]; then
  echo "Wrapping launcher to set GStreamer paths..."
  mv "$MACOS_DIR/TChat" "$MACOS_DIR/TChat.bin"
  cat > "$MACOS_DIR/TChat" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRAMEWORKS="$APP_DIR/Frameworks"
GST_FRAMEWORK="$FRAMEWORKS/GStreamer.framework/Versions/1.0"
GST_PLUGINS="$FRAMEWORKS/gst-plugins"
export DYLD_FRAMEWORK_PATH="$FRAMEWORKS${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
export DYLD_FALLBACK_LIBRARY_PATH="$FRAMEWORKS${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
if [ -d "$GST_FRAMEWORK" ]; then
  export GST_PLUGIN_SYSTEM_PATH_1_0="$GST_FRAMEWORK/lib/gstreamer-1.0"
  export GST_PLUGIN_PATH="$GST_PLUGINS:$GST_PLUGIN_SYSTEM_PATH_1_0"
  export GST_PLUGIN_SCANNER="$GST_FRAMEWORK/libexec/gstreamer-1.0/gst-plugin-scanner"
fi
exec "$APP_DIR/MacOS/TChat.bin"
EOF
  chmod +x "$MACOS_DIR/TChat"
else
  echo "Warning: TChat executable not found in bundle"
fi

echo "Packaged app: $APP_DIR"

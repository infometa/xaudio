#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT_DIR/native/build/gst-plugins"
GST_ROOT="${GST_ROOT:-/Library/Frameworks/GStreamer.framework}"

python3 -m pip install --upgrade pyinstaller

pyinstaller \
  --name TChat \
  --windowed \
  --add-data "$ROOT_DIR/models:models" \
  --add-binary "$PLUGIN_DIR/*.dylib:gst-plugins" \
  --paths "$ROOT_DIR" \
  -m app.main

echo "Copy GStreamer runtime from $GST_ROOT into dist/TChat.app as needed."

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/native/build"

WEBRTC_ROOT="${WEBRTC_ROOT:-}"
WEBRTC_INCLUDE_DIR="${WEBRTC_INCLUDE_DIR:-}"
WEBRTC_LIB_DIR="${WEBRTC_LIB_DIR:-}"
WEBRTC_LIBS="${WEBRTC_LIBS:-}"
ONNXRUNTIME_ROOT="${ONNXRUNTIME_ROOT:-}"
ONNXRUNTIME_INCLUDE_DIR="${ONNXRUNTIME_INCLUDE_DIR:-}"
ONNXRUNTIME_LIB_DIR="${ONNXRUNTIME_LIB_DIR:-}"
ONNXRUNTIME_LIBS="${ONNXRUNTIME_LIBS:-}"

# Local defaults; override by exporting before running this script.
if [[ -z "$WEBRTC_ROOT" ]]; then
  if [[ -d "$HOME/webrtc/src" ]]; then
    WEBRTC_ROOT="$HOME/webrtc/src"
  elif [[ -d "$HOME/webrtc" ]]; then
    WEBRTC_ROOT="$HOME/webrtc"
  fi
fi

if [[ -z "$WEBRTC_INCLUDE_DIR" && -n "$WEBRTC_ROOT" ]]; then
  if [[ -f "$WEBRTC_ROOT/api/audio/audio_processing.h" ]]; then
    WEBRTC_INCLUDE_DIR="$WEBRTC_ROOT"
  elif [[ -f "$WEBRTC_ROOT/src/api/audio/audio_processing.h" ]]; then
    WEBRTC_INCLUDE_DIR="$WEBRTC_ROOT/src"
  fi
fi

if [[ -z "$WEBRTC_LIB_DIR" && -n "$WEBRTC_ROOT" ]]; then
  if [[ -d "$WEBRTC_ROOT/out/Release/obj" ]]; then
    WEBRTC_LIB_DIR="$WEBRTC_ROOT/out/Release/obj"
  elif [[ -d "$WEBRTC_ROOT/src/out/Release/obj" ]]; then
    WEBRTC_LIB_DIR="$WEBRTC_ROOT/src/out/Release/obj"
  elif [[ -d "$WEBRTC_ROOT/out/Default/obj" ]]; then
    WEBRTC_LIB_DIR="$WEBRTC_ROOT/out/Default/obj"
  elif [[ -d "$WEBRTC_ROOT/src/out/Default/obj" ]]; then
    WEBRTC_LIB_DIR="$WEBRTC_ROOT/src/out/Default/obj"
  fi
fi

if [[ -z "$ONNXRUNTIME_ROOT" ]]; then
  for candidate in "$ROOT_DIR"/onnxruntime-osx-arm64-* "$ROOT_DIR"/onnxruntime-osx-x64-* "$ROOT_DIR"/onnxruntime-osx-*; do
    if [[ -d "$candidate" ]]; then
      ONNXRUNTIME_ROOT="$candidate"
      break
    fi
  done
fi

if [[ -z "$ONNXRUNTIME_INCLUDE_DIR" && -n "$ONNXRUNTIME_ROOT" ]]; then
  if [[ -d "$ONNXRUNTIME_ROOT/include" ]]; then
    ONNXRUNTIME_INCLUDE_DIR="$ONNXRUNTIME_ROOT/include"
  fi
fi

if [[ -z "$ONNXRUNTIME_LIB_DIR" && -n "$ONNXRUNTIME_ROOT" ]]; then
  if [[ -d "$ONNXRUNTIME_ROOT/lib" ]]; then
    ONNXRUNTIME_LIB_DIR="$ONNXRUNTIME_ROOT/lib"
  fi
fi

export WEBRTC_ROOT WEBRTC_INCLUDE_DIR WEBRTC_LIB_DIR WEBRTC_LIBS
export ONNXRUNTIME_ROOT ONNXRUNTIME_INCLUDE_DIR ONNXRUNTIME_LIB_DIR ONNXRUNTIME_LIBS

cmake -S "$ROOT_DIR/native" -B "$BUILD_DIR" \
  -DCMAKE_BUILD_TYPE=Release \
  -DWEBRTC_ROOT="$WEBRTC_ROOT" \
  -DWEBRTC_INCLUDE_DIR="$WEBRTC_INCLUDE_DIR" \
  -DWEBRTC_LIB_DIR="$WEBRTC_LIB_DIR" \
  -DWEBRTC_LIBS="$WEBRTC_LIBS" \
  -DONNXRUNTIME_ROOT="$ONNXRUNTIME_ROOT" \
  -DONNXRUNTIME_INCLUDE_DIR="$ONNXRUNTIME_INCLUDE_DIR" \
  -DONNXRUNTIME_LIB_DIR="$ONNXRUNTIME_LIB_DIR" \
  -DONNXRUNTIME_LIBS="$ONNXRUNTIME_LIBS"

cmake --build "$BUILD_DIR" --config Release

echo "Plugins built to: $BUILD_DIR/gst-plugins"

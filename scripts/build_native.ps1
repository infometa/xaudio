$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Root "native\build"

$WebRtcRoot = $env:WEBRTC_ROOT
$WebRtcInclude = $env:WEBRTC_INCLUDE_DIR
$WebRtcLibDir = $env:WEBRTC_LIB_DIR
$WebRtcLibs = $env:WEBRTC_LIBS
$OnnxRoot = $env:ONNXRUNTIME_ROOT
$OnnxInclude = $env:ONNXRUNTIME_INCLUDE_DIR
$OnnxLibDir = $env:ONNXRUNTIME_LIB_DIR
$OnnxLibs = $env:ONNXRUNTIME_LIBS

cmake -S "$Root\native" -B $Build -DCMAKE_BUILD_TYPE=Release `
  -DWEBRTC_ROOT=$WebRtcRoot `
  -DWEBRTC_INCLUDE_DIR=$WebRtcInclude `
  -DWEBRTC_LIB_DIR=$WebRtcLibDir `
  -DWEBRTC_LIBS=$WebRtcLibs `
  -DONNXRUNTIME_ROOT=$OnnxRoot `
  -DONNXRUNTIME_INCLUDE_DIR=$OnnxInclude `
  -DONNXRUNTIME_LIB_DIR=$OnnxLibDir `
  -DONNXRUNTIME_LIBS=$OnnxLibs
cmake --build $Build --config Release

Write-Host "Plugins built to: $Build\gst-plugins"

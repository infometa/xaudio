$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Root "native\build"

# Windows build paths (edit as needed)
$GstRoot = "D:\gstreamer\1.0\msvc_x86_64"
$WebRtcRoot = "D:\code\webrtc\src"
$OnnxRoot = "D:\code\xaudio\onnxruntime-win-x64-1.17.1"

# Optional env overrides
if ($env:GSTREAMER_ROOT) { $GstRoot = $env:GSTREAMER_ROOT }
if (-not $GstRoot -and $env:GST_ROOT) { $GstRoot = $env:GST_ROOT }
if ($env:WEBRTC_ROOT) { $WebRtcRoot = $env:WEBRTC_ROOT }
if ($env:ONNXRUNTIME_ROOT) { $OnnxRoot = $env:ONNXRUNTIME_ROOT }
if ($GstRoot -and (Test-Path $GstRoot)) {
    $env:PATH = "$GstRoot\bin;$env:PATH"
    $env:PKG_CONFIG_PATH = "$GstRoot\lib\pkgconfig"
} else {
    Write-Warning "GSTREAMER_ROOT 未设置或路径不存在：$GstRoot"
    $GstRoot = $null
}

$WebRtcInclude = $env:WEBRTC_INCLUDE_DIR
$WebRtcLibDir = $env:WEBRTC_LIB_DIR
$WebRtcLibs = $env:WEBRTC_LIBS
$OnnxInclude = $env:ONNXRUNTIME_INCLUDE_DIR
$OnnxLibDir = $env:ONNXRUNTIME_LIB_DIR
$OnnxLibs = $env:ONNXRUNTIME_LIBS

if (-not $WebRtcRoot -or -not (Test-Path $WebRtcRoot)) {
    throw "WEBRTC_ROOT 未设置或路径不存在：$WebRtcRoot"
}
if (-not $WebRtcInclude) {
    $WebRtcInclude = $WebRtcRoot
}
if (-not $WebRtcLibDir) {
    $Candidate = Join-Path $WebRtcRoot "out\Release\obj"
    if (Test-Path $Candidate) {
        $WebRtcLibDir = $Candidate
    }
}
if (-not $OnnxRoot -or -not (Test-Path $OnnxRoot)) {
    throw "ONNXRUNTIME_ROOT 未设置或路径不存在：$OnnxRoot"
}
if (-not $OnnxInclude) {
    $Candidate = Join-Path $OnnxRoot "include"
    if (Test-Path $Candidate) {
        $OnnxInclude = $Candidate
    }
}
if (-not $OnnxLibDir) {
    $Candidate = Join-Path $OnnxRoot "lib"
    if (Test-Path $Candidate) {
        $OnnxLibDir = $Candidate
    }
}

$Generator = $env:CMAKE_GENERATOR
if (-not $Generator) {
    if (Get-Command ninja -ErrorAction SilentlyContinue) {
        $Generator = "Ninja"
    }
}

$Args = @(
    "-S", "$Root\native",
    "-B", $Build,
    "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_CXX_STANDARD=20",
    "-DWEBRTC_ROOT=$WebRtcRoot",
    "-DWEBRTC_INCLUDE_DIR=$WebRtcInclude"
)
if ($WebRtcLibDir) {
    $Args += "-DWEBRTC_LIB_DIR=$WebRtcLibDir"
}
if ($WebRtcLibs) {
    $Args += "-DWEBRTC_LIBS=$WebRtcLibs"
}
if ($OnnxRoot) {
    $Args += "-DONNXRUNTIME_ROOT=$OnnxRoot"
}
if ($OnnxInclude) {
    $Args += "-DONNXRUNTIME_INCLUDE_DIR=$OnnxInclude"
}
if ($OnnxLibDir) {
    $Args += "-DONNXRUNTIME_LIB_DIR=$OnnxLibDir"
}
if ($OnnxLibs) {
    $Args += "-DONNXRUNTIME_LIBS=$OnnxLibs"
}
if ($Generator) {
    $Args += "-G"
    $Args += $Generator
}

cmake @Args
cmake --build $Build --config Release

Write-Host "Plugins built to: $Build\gst-plugins"

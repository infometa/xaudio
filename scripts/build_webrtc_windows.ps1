$ErrorActionPreference = "Stop"

param(
    [string]$WebRtcRoot = "",
    [string]$OutDir = "out\Release",
    [switch]$UseStaticCrt
)

if (-not $WebRtcRoot) {
    $WebRtcRoot = $env:WEBRTC_ROOT
}
if (-not $WebRtcRoot -or -not (Test-Path $WebRtcRoot)) {
    throw "WEBRTC_ROOT 未设置或路径不存在：$WebRtcRoot"
}

Push-Location $WebRtcRoot
try {
    $useStatic = "false"
    if ($UseStaticCrt) {
        $useStatic = "true"
    }
    $gnArgs = @(
        "is_debug=false",
        "target_cpu=\"x64\"",
        "rtc_include_tests=false",
        "rtc_build_examples=false",
        "is_clang=false",
        "use_lld=false",
        "use_custom_libcxx=false",
        "use_static_crt=$useStatic"
    ) -join " "
    Write-Host "gn gen $OutDir --args=`"$gnArgs`""
    gn gen $OutDir --args="$gnArgs"
    ninja -C $OutDir modules/audio_processing:audio_processing
    ninja -C $OutDir api/audio:builtin_audio_processing_builder
} finally {
    Pop-Location
}

Write-Host "WebRTC APM build complete: $WebRtcRoot\$OutDir"

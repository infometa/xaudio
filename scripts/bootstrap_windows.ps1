$ErrorActionPreference = "Stop"

param(
    [string]$GstVersion = "1.26.10",
    [string]$GstRoot = "C:\gstreamer\1.0\msvc_x86_64",
    [switch]$SkipGstreamer,
    [switch]$SkipBuild,
    [switch]$SkipPackage
)

$Root = Split-Path -Parent $PSScriptRoot
$DownloadDir = Join-Path $Root ".deps\gstreamer"

function Download-File([string]$Url, [string]$Dest) {
    if (Test-Path $Dest) {
        return
    }
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Dest
}

function Extract-Msi([string]$MsiPath, [string]$TargetDir) {
    if (-not (Test-Path $TargetDir)) {
        New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    }
    $args = "/a `"$MsiPath`" /qn TARGETDIR=`"$TargetDir`""
    Write-Host "Extracting $MsiPath -> $TargetDir"
    $proc = Start-Process msiexec -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "MSI extract failed: $MsiPath (exit $($proc.ExitCode))"
    }
}

if (-not $SkipGstreamer) {
    $BaseUrl = "https://gstreamer.freedesktop.org/data/pkg/windows/$GstVersion/msvc"
    $RuntimeMsi = "gstreamer-1.0-msvc-x86_64-$GstVersion.msi"
    $DevelMsi = "gstreamer-1.0-devel-msvc-x86_64-$GstVersion.msi"
    $RuntimePath = Join-Path $DownloadDir $RuntimeMsi
    $DevelPath = Join-Path $DownloadDir $DevelMsi

    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    Download-File "$BaseUrl/$RuntimeMsi" $RuntimePath
    Download-File "$BaseUrl/$DevelMsi" $DevelPath

    Extract-Msi $RuntimePath $GstRoot
    Extract-Msi $DevelPath $GstRoot
}

$env:GSTREAMER_ROOT = $GstRoot
if ($env:GSTREAMER_ROOT -and (Test-Path $env:GSTREAMER_ROOT)) {
    $env:PATH = "$env:GSTREAMER_ROOT\bin;$env:PATH"
    $env:PKG_CONFIG_PATH = "$env:GSTREAMER_ROOT\lib\pkgconfig"
} else {
    Write-Warning "GSTREAMER_ROOT 未设置或路径不存在：$env:GSTREAMER_ROOT"
}

if (-not $env:WEBRTC_ROOT) {
    throw "WEBRTC_ROOT 未设置，请先设置 WebRTC 源码路径"
}
if (-not $env:ONNXRUNTIME_ROOT) {
    throw "ONNXRUNTIME_ROOT 未设置，请先设置 ONNX Runtime 路径"
}

if (-not $SkipBuild) {
    & "$Root\scripts\build_native.ps1"
}

if (-not $SkipPackage) {
    & "$Root\scripts\package_windows.ps1"
}

Write-Host "Windows bootstrap 完成"

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$PluginDir = Join-Path $Root "native\build\gst-plugins"
$GstRoot = $env:GSTREAMER_ROOT
if (-not $GstRoot) {
    $GstRoot = $env:GST_ROOT
}
$OnnxRoot = $env:ONNXRUNTIME_ROOT
$DistRoot = Join-Path $Root "dist\TChat"

python -m pip install --upgrade pyinstaller

pyinstaller --name TChat --windowed `
  --add-data "$Root\models;models" `
  --add-binary "$PluginDir\*.dll;gst-plugins" `
  --paths $Root `
  -m app.main

if (-not (Test-Path $DistRoot)) {
    throw "未找到打包输出目录：$DistRoot"
}

if (Test-Path $PluginDir) {
    $DstPlugins = Join-Path $DistRoot "gst-plugins"
    New-Item -ItemType Directory -Force -Path $DstPlugins | Out-Null
    Copy-Item "$PluginDir\*.dll" -Destination $DstPlugins -Force -ErrorAction SilentlyContinue
} else {
    Write-Warning "未找到本地插件目录：$PluginDir"
}

if ($GstRoot -and (Test-Path $GstRoot)) {
    Copy-Item "$GstRoot\bin\*.dll" -Destination $DistRoot -Force -ErrorAction SilentlyContinue
    Copy-Item "$GstRoot\bin\gst-plugin-scanner.exe" -Destination $DistRoot -Force -ErrorAction SilentlyContinue
    $GstPluginDir = Join-Path $GstRoot "lib\gstreamer-1.0"
    if (Test-Path $GstPluginDir) {
        $DstGstPlugins = Join-Path $DistRoot "gstreamer-1.0"
        New-Item -ItemType Directory -Force -Path $DstGstPlugins | Out-Null
        Copy-Item "$GstPluginDir\*.dll" -Destination $DstGstPlugins -Force -ErrorAction SilentlyContinue
    }
    $GstTypelibDir = Join-Path $GstRoot "lib\girepository-1.0"
    if (Test-Path $GstTypelibDir) {
        $DstTypelibs = Join-Path $DistRoot "girepository-1.0"
        New-Item -ItemType Directory -Force -Path $DstTypelibs | Out-Null
        Copy-Item "$GstTypelibDir\*.typelib" -Destination $DstTypelibs -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Warning "未设置 GSTREAMER_ROOT/GST_ROOT 或路径不存在：$GstRoot"
}

if ($OnnxRoot -and (Test-Path $OnnxRoot)) {
    $OnnxDll = Join-Path $OnnxRoot "lib\onnxruntime.dll"
    if (-not (Test-Path $OnnxDll)) {
        $OnnxDll = Join-Path $OnnxRoot "onnxruntime.dll"
    }
    if (Test-Path $OnnxDll) {
        Copy-Item $OnnxDll -Destination $DistRoot -Force
    } else {
        Write-Warning "未找到 onnxruntime.dll：$OnnxRoot"
    }
}

$Runner = @"
@echo off
set DIR=%~dp0
set GST_PLUGIN_PATH=%DIR%gst-plugins;%DIR%gstreamer-1.0
set GST_PLUGIN_SYSTEM_PATH_1_0=%DIR%gstreamer-1.0
set GST_PLUGIN_SCANNER=%DIR%gst-plugin-scanner.exe
set GI_TYPELIB_PATH=%DIR%girepository-1.0
set PATH=%DIR%;%PATH%
start "" "%DIR%TChat.exe"
"@
$RunnerPath = Join-Path $DistRoot "run_tchat.bat"
$Runner | Out-File -FilePath $RunnerPath -Encoding ASCII

Write-Host "打包完成：$DistRoot"

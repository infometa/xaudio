$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$PluginDir = Join-Path $Root "native\build\gst-plugins"
$GstRoot = $env:GST_ROOT

python -m pip install --upgrade pyinstaller

pyinstaller --name TChat --windowed `
  --add-data "$Root\models;models" `
  --add-binary "$PluginDir\*.dll;gst-plugins" `
  --paths $Root `
  -m app.main

Write-Host "Copy GStreamer runtime from %GST_ROOT% into dist\TChat as needed."

# TChat P2P Voice (Windows/macOS)

Python + PySide6 + GStreamer peer-to-peer voice client. Direct IP:Port only, RTP/UDP transport. No NAT traversal, no multiparty, no screen sharing, no end-to-end encryption.

## MVP Features

- 48 kHz / mono / 10 ms frames (480 samples).
- Uplink: Capture -> AEC3 -> DeepFilterNet -> Limiter -> Opus -> RTP/UDP.
- Downlink: UDP/RTP -> jitter buffer -> Opus -> playout, with AEC render reference.
- Silero VAD (ONNX Runtime Python) as sidecar tagger for speaking indicator.
- Metrics: DFN P50/P95, bypass count, queue depth, jitter depth, mic->send latency.

## Layout

```
app/              Python app
native/           GStreamer native plugins (AEC3/DFN)
models/           ONNX model placeholders
scripts/          Build/package scripts
README.md
requirements.txt
```

## Dependencies

### Common

- Python 3.11+
- GStreamer 1.22+
- ONNX Runtime (C API)
- WebRTC AudioProcessing (AEC3)

### macOS

1) Install GStreamer (pkg):
- Use **Development Installer** if you will build native plugins (headers/libs/tools).
- Use **Runtime Installer** if you only want to run the app.
- https://gstreamer.freedesktop.org/download/
- If using the official pkg, export `PKG_CONFIG_PATH` to the framework before building:

```bash
export GST_ROOT="/Library/Frameworks/GStreamer.framework"
export PKG_CONFIG_PATH="$GST_ROOT/Versions/1.0/lib/pkgconfig:$PKG_CONFIG_PATH"
export PATH="$GST_ROOT/Versions/1.0/bin:$PATH"
```

2) Python deps (conda):

```bash
conda create -n tchat python=3.11
conda activate tchat
pip install -r requirements.txt
```

If `pip` fails on `PyGObject`/`pycairo` (common with conda), install those from conda-forge instead:

```bash
conda install -c conda-forge pygobject pycairo gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav gst-python
```

3) ONNX Runtime C API:
- Download from https://github.com/microsoft/onnxruntime/releases
- macOS Apple Silicon: `onnxruntime-osx-arm64-<version>.tgz`
- macOS Intel: `onnxruntime-osx-x64-<version>.tgz`
- Extract and set `ONNXRUNTIME_ROOT` to the extracted folder.

Example:
```bash
export ONNXRUNTIME_ROOT=/Users/haifeng/Desktop/code/project/onnxruntime-osx-arm64-1.23.2
```

4) WebRTC APM (detailed):

Install depot_tools:
In this project depot_tools location is "/Users/haifeng/Desktop/code/project/depot_tools"
```bash
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$PWD/depot_tools:$PATH"
```

Fetch and sync WebRTC:
```bash
mkdir -p ~/webrtc && cd ~/webrtc
fetch --nohooks webrtc
gclient sync
cd src
```

Build AudioProcessing (macOS arm64 example; use target_cpu="x64" on Intel):
```bash
gn gen out/Release --args='is_debug=false target_cpu="arm64" rtc_include_tests=false rtc_build_examples=false use_lld=false use_custom_libcxx=false use_clang_modules=false'
ninja -C out/Release modules/audio_processing:audio_processing
ninja -C out/Release api/audio:builtin_audio_processing_builder
ninja -C out/Release api/environment:environment_factory
```
Note: the real APM library is `out/Release/obj/modules/audio_processing/libaudio_processing.a`. `out/Release/obj/api/audio/libaudio_processing.a` may be a stub and should not be linked.
If you see `thin archive` in `file libaudio_processing.a`, rebuild with `use_lld=false` (lld builds thin archives; Apple ld64 can't link them).
If you see symbols like `std::__Cr::basic_string...` during link, rebuild with `use_custom_libcxx=false` so WebRTC uses system libc++.
If `ninja` fails building `module.pcm` under `buildtools/third_party/libc++`, rebuild with `use_clang_modules=false`.

Point CMake to headers/libs:
```bash
export WEBRTC_INCLUDE_DIR=~/webrtc/src
export WEBRTC_LIB_DIR=~/webrtc/src/out/Release/obj
export WEBRTC_LIBS=""
```
If you previously exported `WEBRTC_LIBS=audio_processing`, unset it or set it to empty and delete `native/build` to avoid stale CMake cache:
```bash
unset WEBRTC_LIBS
rm -rf native/build
```

If you see `ld: unknown file type ... libaudio_processing.a`, it means thin archives were built; delete `out/Release` and re-run `gn gen` with `use_lld=false`.
If you already packaged headers/libs into a prefix, set `WEBRTC_ROOT` to that prefix (with `include/` and `lib/`).
If you hit linker errors for missing symbols, add the required libs to `WEBRTC_LIBS` (e.g. `rtc_base` / `absl_*`) from `out/Release/obj`.

### Windows

1) Install GStreamer (MSVC build) and set `PATH` / `GST_PLUGIN_PATH`.
   - Development package is required to build native plugins.
   - Runtime package is enough to run the app.

2) Python deps (conda):

```powershell
conda create -n tchat python=3.11
conda activate tchat
pip install -r requirements.txt
```

If `pip` fails on `PyGObject`/`pycairo`, install them from conda-forge:

```powershell
conda install -c conda-forge pygobject pycairo gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav gst-python
```

3) PyGObject on Windows: use gvsbuild or MSYS2 packages, aligned with the installed GStreamer SDK.

4) ONNX Runtime C API: download Windows package and set `ONNXRUNTIME_ROOT`.

Example:
```powershell
$env:ONNXRUNTIME_ROOT="C:\path\to\onnxruntime-win-x64-<version>"
```

5) WebRTC APM (detailed):

Install depot_tools:
```powershell
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
$env:PATH="$PWD\\depot_tools;$env:PATH"
```

Fetch and sync WebRTC:
```powershell
mkdir $HOME\\webrtc
cd $HOME\\webrtc
fetch --nohooks webrtc
gclient sync
cd src
```

Build AudioProcessing (x64):
```powershell
gn gen out/Release --args="is_debug=false target_cpu=\"x64\" rtc_include_tests=false rtc_build_examples=false"
ninja -C out/Release modules/audio_processing:audio_processing
ninja -C out/Release api/audio:builtin_audio_processing_builder
```

Point CMake to headers/libs:

```powershell
$env:WEBRTC_INCLUDE_DIR="C:\path\to\webrtc\src"
$env:WEBRTC_LIB_DIR="C:\path\to\webrtc\src\out\Release\obj"
$env:WEBRTC_LIBS=""
```

If you hit linker errors for missing symbols, add the required libs to `WEBRTC_LIBS` (e.g. `rtc_base` / `absl_*`) from `out\Release\obj`.

## Build Native Plugins (AEC3 / DeepFilterNet)

```bash
export WEBRTC_ROOT=/path/to/webrtc
export ONNXRUNTIME_ROOT=/path/to/onnxruntime
./scripts/build_native.sh
```

Windows:

```powershell
$env:WEBRTC_ROOT="C:\path\to\webrtc"
$env:ONNXRUNTIME_ROOT="C:\path\to\onnxruntime"
.\scripts\build_native.ps1
```

Output: `native/build/gst-plugins/`.

## Replace Model Files

### DeepFilterNet
- Recommended: use DeepFilterNet3 ONNX pack (enc/erb_dec/df_dec + config.ini).
- Put files in `models/DeepFilterNet/` (or extract `DeepFilterNet3_ll_onnx.tar.gz` there).
- The plugin auto-detects this directory and loads the 3 models + config.

Fallback (legacy single-model):
- Path: `models/deepfilternet.onnx` (placeholder)
- Default I/O names: `input` / `output`
- Expected shape: `[1, 1, 480]` float32

If you use a single-model export, make sure I/O names and shapes match, or set plugin properties `input-name` / `output-name`.

### Silero VAD
- Default path: `models/silero_vad.onnx` (placeholder)
- Default input shape: `[1, 1, 160]` float32 (10 ms @ 16 kHz)
- Output: scalar probability

If you use the standard Silero model (often 512 samples), replace the model and adjust input shape accordingly.

Note: 48k -> 16k downsampling in VAD thread uses simple decimation. Replace with a high-quality resampler for production.

## Run

1) Set plugin path:

```bash
export GST_PLUGIN_PATH="$(pwd)/native/build/gst-plugins"
```

2) Start app:

```bash
python3 -m app.main
```

3) Connect two machines:
- Side A: **Start Listen** (local RTP port default 5004)
- Side B: enter A's IP:Port, **Call**
- Signaling uses RTP port + 1

## Signaling

- HELLO / ACK / KEEPALIVE / BYE
- Glare handled by random tie-breaker
- No NAT traversal

## Pipelines

Uplink:
```
wasapi/osx capture -> AEC3 -> tee -> (VAD appsink) -> DeepFilterNet -> limiter -> Opus -> RTP -> UDP
```

Downlink:
```
UDP -> RTP jitter buffer -> Opus decode -> tee -> (playout) + (AEC3 render reference)
```

## Metrics

UI shows:
- DFN P50/P95 (ms)
- DFN bypass count
- Queue depth per queue
- Jitter buffer depth (if available)
- Mic->send latency estimate

## Packaging

### macOS

```bash
./scripts/package_macos.sh
```

Copy the GStreamer runtime into the app bundle and set `GST_PLUGIN_PATH` to `gst-plugins`.

### Windows

```powershell
.\scripts\package_windows.ps1
```

Copy the GStreamer runtime and plugin DLLs into `dist\TChat`.

## Troubleshooting

- No audio: verify `GST_PLUGIN_PATH` includes `native/build/gst-plugins` and GStreamer version matches.
- Echo: ensure AEC3 render reference is connected (downlink decode tee -> AEC render pad).
- Plugin not found: set `GST_DEBUG=3` and check for `webrtcaec3` / `deepfilternet`.
- Port busy: change local RTP port; signaling uses RTP+1.

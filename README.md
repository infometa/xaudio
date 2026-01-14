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

#### Windows Build (Step-by-step)

1) Install build tools:
   - Visual Studio 2022 (Desktop development with C++)
   - CMake 3.16+
   - Ninja (optional, faster)

2) Install GStreamer (MSVC build) and set the env:
```powershell
$env:GSTREAMER_ROOT="C:\gstreamer\1.0\msvc_x86_64"
$env:PATH="$env:GSTREAMER_ROOT\bin;$env:PATH"
$env:PKG_CONFIG_PATH="$env:GSTREAMER_ROOT\lib\pkgconfig"
```

3) Create Python env and install deps:
```powershell
conda create -n tchat python=3.11
conda activate tchat
pip install -r requirements.txt
```

4) Download ONNX Runtime C API and set:
```powershell
$env:ONNXRUNTIME_ROOT="C:\path\to\onnxruntime-win-x64-<version>"
```

5) Build WebRTC APM:
```powershell
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
$env:PATH="$PWD\\depot_tools;$env:PATH"
mkdir $HOME\\webrtc
cd $HOME\\webrtc
fetch --nohooks webrtc
gclient sync
cd src
gn gen out/Release --args="is_debug=false target_cpu=\"x64\" rtc_include_tests=false rtc_build_examples=false"
ninja -C out/Release modules/audio_processing:audio_processing
ninja -C out/Release api/audio:builtin_audio_processing_builder
```

6) In the repo root, set WebRTC paths:
```powershell
$env:WEBRTC_ROOT="C:\path\to\webrtc\src"
```

7) Optional link switches (default is obj-only, recommended):
```powershell
$env:WEBRTC_LINK_LIBS="OFF"
$env:WEBRTC_LINK_OBJS="ON"
```

8) Build native plugins:
```powershell
.\scripts\build_native.ps1
```

9) Run with plugins:
```powershell
$env:GST_PLUGIN_PATH="$PWD\\native\\build\\gst-plugins"
python -m app.main
```

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

Note: 48k -> 16k downsampling for VAD uses GStreamer `audioresample` with `quality=4`. Increase quality if you need more accuracy at higher CPU cost.

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

## Runtime Knobs

- `TCHAT_DISABLE_AEC=1` / `TCHAT_DISABLE_DFN=1`: force bypass.
- `TCHAT_DISABLE_AGC=1`: disable WebRTC AGC (gain control).
- `TCHAT_AGC_INPUT_VOLUME`: enable AGC input volume controller (0/1).
- `TCHAT_AGC_HEADROOM_DB`: AGC headroom in dB (default 6).
- `TCHAT_AGC_MAX_GAIN_DB`: AGC max gain in dB (default 30).
- `TCHAT_AGC_INITIAL_GAIN_DB`: AGC initial gain in dB (default 10).
- `TCHAT_AGC_MAX_NOISE_DBFS`: AGC max output noise level (default -50).
- `TCHAT_AEC_AUTO_DELAY`: enable automatic AEC delay estimation (default 1).
- `TCHAT_AEC_DELAY_MS`: AEC stream delay in ms (0-500).
- `TCHAT_HPF_ENABLED`: enable high-pass filter (default 1).
- `TCHAT_HPF_CUTOFF_HZ`: HPF cutoff (Hz, default 100).
- `TCHAT_DFN_MIX`: DFN dry/wet mix (0.0-1.0, default 0.85).
- `TCHAT_DFN_POST_FILTER`: DFN post filter strength (0.0-1.0, default 0.1).
- `TCHAT_DFN_VAD_LINK`: link VAD to DFN mix (default 1).
- `TCHAT_DFN_MIX_SPEECH`: DFN mix while speaking (default 0.8).
- `TCHAT_DFN_MIX_SILENCE`: DFN mix while silent (default 1.0).
- `TCHAT_DFN_MIX_SMOOTHING`: DFN mix smoothing (default 0.15).
- `TCHAT_DFN_ALLOW_DEFAULT_OUTPUT`: allow fallback to `emb` when DFN3 output names mismatch (default 0).
- `TCHAT_EQ_ENABLED`: enable 3-band EQ (default 1).
- `TCHAT_EQ_LOW_DB` / `TCHAT_EQ_MID_DB` / `TCHAT_EQ_HIGH_DB`: EQ gains (dB, default -2 / 2 / 1).
- `TCHAT_CNG_ENABLED`: enable comfort noise (default 1).
- `TCHAT_CNG_LEVEL_DB`: comfort noise level in dBFS (default -62).
- `TCHAT_CNG_FADE_MS`: comfort noise fade time (ms, default 15).
- `TCHAT_LIMITER_THRESHOLD_DB`: limiter threshold in dB (default -1.0).
- `TCHAT_LIMITER_ATTACK_MS` / `TCHAT_LIMITER_RELEASE_MS`: limiter time constants (default 5 / 80).
- `TCHAT_OPUS_BITRATE`: Opus bitrate (bps, default 48000).
- `TCHAT_OPUS_COMPLEXITY`: Opus complexity (0-10, default 10).
- `TCHAT_OPUS_FEC`: enable Opus in-band FEC (0/1, default 1).
- `TCHAT_OPUS_DTX`: enable Opus DTX (0/1).
- `TCHAT_OPUS_PACKET_LOSS`: expected packet loss percentage for FEC tuning (default 5).
- `TCHAT_TARGET_SAMPLE_RATE`: target processing sample rate (Hz, default 48000).
- `TCHAT_JITTER_LATENCY_MS`: base jitter buffer latency in ms (default 30).
- `TCHAT_JITTER_MIN_MS` / `TCHAT_JITTER_MAX_MS`: clamp jitter buffer range.
- `TCHAT_JITTER_SMOOTHING`: smoothing factor for jitter adaptation (default 0.9).
- `TCHAT_JITTER_ADJUST_INTERVAL`: min seconds between jitter updates (default 2.0).
- `TCHAT_SIGNAL_BIND`: signaling bind IP (default 0.0.0.0).
- `TCHAT_SIGNAL_ALLOWLIST`: comma-separated IP allowlist (optional).
- `TCHAT_SIGNAL_TOKEN`: shared signaling token (optional).
- `TCHAT_SIGNAL_RCVBUF` / `TCHAT_SIGNAL_SNDBUF`: UDP buffer sizes in bytes.
- `TCHAT_KEEPALIVE_INTERVAL`: keepalive send interval in seconds (default 1.0).
- `TCHAT_KEEPALIVE_TIMEOUT`: timeout window for missing keepalive (default 6.0).
- `TCHAT_KEEPALIVE_MAX_MISSES`: disconnect after N timeout windows (default 5).
- `TCHAT_DEFAULT_LOCAL_PORT` / `TCHAT_DEFAULT_REMOTE_IP` / `TCHAT_DEFAULT_REMOTE_PORT`: UI defaults.
- `TCHAT_GST_PLUGIN_PATH` / `TCHAT_HOMEBREW_GST_PATH`: extra GStreamer plugin paths.

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

## Dependency Locking

Generate a lock file inside the active environment:

```bash
./scripts/freeze_requirements.sh
```

## Troubleshooting

- No audio: verify `GST_PLUGIN_PATH` includes `native/build/gst-plugins` and GStreamer version matches.
- Echo: ensure AEC3 render reference is connected (downlink decode tee -> AEC render pad).
- Plugin not found: set `GST_DEBUG=3` and check for `webrtcaec3` / `deepfilternet`.
- Port busy: change local RTP port; signaling uses RTP+1.
- VAD too insensitive: adjust `VAD_PROB_ON/VAD_PROB_OFF` or `VAD_ENERGY_DB_ON/VAD_ENERGY_DB_OFF` environment variables.
- Debugging: disable AEC/DFN with `TCHAT_DISABLE_AEC=1` or `TCHAT_DISABLE_DFN=1`.

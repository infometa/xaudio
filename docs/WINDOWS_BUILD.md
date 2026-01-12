# TChat Windows 编译运行指南

## 快速开始（仅运行，不编译原生插件）

如果只需要运行应用（不需要 AEC3 回声消除和 DeepFilterNet 降噪），可以跳过原生插件编译。

### 1. 安装 GStreamer Runtime

1. 下载 GStreamer MSVC Runtime: https://gstreamer.freedesktop.org/download/
2. 选择 **1.22+** 版本的 **MSVC 64-bit runtime installer**
3. 安装到默认路径 `C:\gstreamer\1.0\msvc_x86_64`

### 2. 安装 Python 环境

```powershell
# 使用 conda
conda create -n tchat python=3.11
conda activate tchat

# 安装依赖
pip install PySide6 numpy onnxruntime sounddevice

# 安装 PyGObject (用于 GStreamer Python 绑定)
conda install -c conda-forge pygobject gst-python
```

### 3. 设置环境变量

```powershell
# GStreamer 路径
$env:PATH = "C:\gstreamer\1.0\msvc_x86_64\bin;$env:PATH"
$env:GST_PLUGIN_PATH = "C:\gstreamer\1.0\msvc_x86_64\lib\gstreamer-1.0"

# 如果有自定义插件
$env:GST_PLUGIN_PATH = "$PWD\native\build\gst-plugins;$env:GST_PLUGIN_PATH"
```

### 4. 运行应用

```powershell
cd xaudio
python -m app.main
```

---

## 完整编译（包含原生插件）

如果需要 AEC3 回声消除和 DeepFilterNet 降噪功能，需要编译原生 GStreamer 插件。

### 前置条件

- Visual Studio 2022 (带 C++ 桌面开发工作负载)
- CMake 3.20+
- Git

### 1. 安装 GStreamer Development

1. 下载 GStreamer MSVC **Development** installer
2. 安装到 `C:\gstreamer\1.0\msvc_x86_64`
3. 设置环境变量:

```powershell
$env:GSTREAMER_1_0_ROOT_MSVC_X86_64 = "C:\gstreamer\1.0\msvc_x86_64"
$env:PATH = "$env:GSTREAMER_1_0_ROOT_MSVC_X86_64\bin;$env:PATH"
$env:PKG_CONFIG_PATH = "$env:GSTREAMER_1_0_ROOT_MSVC_X86_64\lib\pkgconfig"
```

### 2. 下载 ONNX Runtime

```powershell
# 下载 Windows x64 版本
# https://github.com/microsoft/onnxruntime/releases
# 选择 onnxruntime-win-x64-1.23.2.zip

# 解压到项目目录或其他位置
Expand-Archive onnxruntime-win-x64-1.23.2.zip -DestinationPath C:\libs\

# 设置环境变量
$env:ONNXRUNTIME_ROOT = "C:\libs\onnxruntime-win-x64-1.23.2"
```

### 3. 编译 WebRTC AudioProcessing (可选，用于 AEC3)

这是最复杂的部分。如果不需要回声消除，可以跳过。

```powershell
# 1. 安装 depot_tools
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git C:\depot_tools
$env:PATH = "C:\depot_tools;$env:PATH"

# 2. 获取 WebRTC 源码
mkdir C:\webrtc
cd C:\webrtc
fetch --nohooks webrtc
gclient sync

# 3. 编译 AudioProcessing
cd src
gn gen out/Release --args='is_debug=false target_cpu="x64" rtc_include_tests=false rtc_build_examples=false'
ninja -C out/Release modules/audio_processing:audio_processing
ninja -C out/Release api/audio:builtin_audio_processing_builder

# 4. 设置环境变量
$env:WEBRTC_INCLUDE_DIR = "C:\webrtc\src"
$env:WEBRTC_LIB_DIR = "C:\webrtc\src\out\Release\obj"
```

### 4. 编译原生插件

```powershell
cd xaudio

# 设置环境变量
$env:ONNXRUNTIME_ROOT = "C:\libs\onnxruntime-win-x64-1.23.2"
$env:WEBRTC_INCLUDE_DIR = "C:\webrtc\src"  # 如果编译了 WebRTC
$env:WEBRTC_LIB_DIR = "C:\webrtc\src\out\Release\obj"

# 运行编译脚本
.\scripts\build_native.ps1
```

编译产物在 `native\build\gst-plugins\` 目录。

### 5. 运行

```powershell
# 设置插件路径
$env:GST_PLUGIN_PATH = "$PWD\native\build\gst-plugins;C:\gstreamer\1.0\msvc_x86_64\lib\gstreamer-1.0"

# 运行
python -m app.main
```

---

## 常见问题

### Q: 找不到 GStreamer 模块
```
ModuleNotFoundError: No module named 'gi'
```

**解决**: 使用 conda 安装 PyGObject:
```powershell
conda install -c conda-forge pygobject gst-python
```

### Q: 找不到 GStreamer 插件
```
No element "opusenc"
```

**解决**: 确保 GStreamer 路径正确:
```powershell
$env:PATH = "C:\gstreamer\1.0\msvc_x86_64\bin;$env:PATH"
$env:GST_PLUGIN_PATH = "C:\gstreamer\1.0\msvc_x86_64\lib\gstreamer-1.0"
```

### Q: 音频设备找不到
```
No audio devices found
```

**解决**: 安装 sounddevice:
```powershell
pip install sounddevice
```

### Q: DLL 加载失败
```
ImportError: DLL load failed
```

**解决**: 确保 Visual C++ Redistributable 已安装:
- 下载: https://aka.ms/vs/17/release/vc_redist.x64.exe

---

## 简化测试流程

如果只想快速测试基本功能（不含原生插件）：

```powershell
# 1. 克隆仓库
git clone git@github.com:infometa/xaudio.git
cd xaudio

# 2. 创建 conda 环境
conda create -n tchat python=3.11 -y
conda activate tchat

# 3. 安装依赖
pip install PySide6 numpy onnxruntime sounddevice
conda install -c conda-forge pygobject gst-python gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad -y

# 4. 运行（会缺少 AEC3 和 DeepFilterNet，但基本通话可用）
python -m app.main
```

---

## 目录结构

```
xaudio/
├── app/                          # Python 应用代码
├── models/                       # ONNX 模型 (已包含)
│   ├── silero_vad.onnx          # VAD 模型
│   └── DeepFilterNet/           # 降噪模型
├── native/                       # C++ GStreamer 插件源码
│   ├── CMakeLists.txt
│   ├── deepfilternet/           # DeepFilterNet 插件
│   └── webrtc_aec3/             # AEC3 插件
├── onnxruntime-osx-arm64-1.23.2/ # macOS 版 ONNX Runtime (需下载 Windows 版)
├── scripts/
│   ├── build_native.ps1         # Windows 编译脚本
│   └── run_dev.sh               # macOS 运行脚本
└── requirements.txt
```

## 联系

如有问题，请提 Issue。

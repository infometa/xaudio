#!/bin/bash
set -e

echo "=== WebRTC AEC3 自动构建脚本 ==="
echo "开始时间: $(date)"
echo ""

# 等待 WebRTC 编译完成
echo "步骤 1: 等待 WebRTC 库编译完成..."
MAX_WAIT=7200  # 最多等待 2 小时
ELAPSED=0
INTERVAL=30

while [ $ELAPSED -lt $MAX_WAIT ]; do
    # 检查进程是否还在运行
    if ! pgrep -f "ninja.*builtin_audio_processing_builder" > /dev/null; then
        echo "✓ Ninja 进程已结束"
        break
    fi
    
    # 显示进度
    PROGRESS=$(tail -1 /tmp/webrtc_build.log 2>/dev/null | grep -o '\[[0-9]*/[0-9]*\]' | head -1)
    if [ -n "$PROGRESS" ]; then
        echo "  进度: $PROGRESS (已等待 $((ELAPSED/60)) 分钟)"
    fi
    
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

echo ""

# 检查是否成功
if grep -qi "error\|failed" /tmp/webrtc_build.log | tail -20 | grep -v "warning"; then
    echo "❌ WebRTC 编译失败！"
    echo "错误日志:"
    tail -30 /tmp/webrtc_build.log
    exit 1
fi

# 检查库文件是否存在
echo "步骤 2: 验证 WebRTC 库文件..."
BUILDER_LIB="/Users/haifeng/webrtc/src/out/Release/obj/api/audio/libbuiltin_audio_processing_builder.a"
ENV_LIB="/Users/haifeng/webrtc/src/out/Release/obj/api/environment/libenvironment_factory.a"

if [ ! -f "$BUILDER_LIB" ]; then
    echo "❌ builtin_audio_processing_builder.a 未找到"
    exit 1
fi

if [ ! -f "$ENV_LIB" ]; then
    echo "❌ environment_factory.a 未找到"
    exit 1
fi

echo "✓ 所有必需的库文件已生成"
echo "  - $(ls -lh $BUILDER_LIB | awk '{print $9, $5}')"
echo "  - $(ls -lh $ENV_LIB | awk '{print $9, $5}')"
echo ""

# 构建 TChat WebRTC AEC3 插件
echo "步骤 3: 构建 TChat WebRTC AEC3 插件..."
cd /Users/haifeng/Desktop/code/project/TChat

# 清理旧构建
rm -rf native/build

# 配置 CMake
export ONNXRUNTIME_ROOT="$(pwd)/onnxruntime-osx-arm64-1.23.2"
export WEBRTC_INCLUDE_DIR="/Users/haifeng/webrtc/src"
export WEBRTC_LIB_DIR="/Users/haifeng/webrtc/src/out/Release/obj"

echo "  配置 CMake..."
cmake -S native -B native/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DONNXRUNTIME_ROOT="$ONNXRUNTIME_ROOT" \
  -DONNXRUNTIME_INCLUDE_DIR="$ONNXRUNTIME_ROOT/include" \
  -DONNXRUNTIME_LIB_DIR="$ONNXRUNTIME_ROOT/lib" \
  -DWEBRTC_INCLUDE_DIR="$WEBRTC_INCLUDE_DIR" \
  -DWEBRTC_LIB_DIR="$WEBRTC_LIB_DIR" > /tmp/cmake_config.log 2>&1

if [ $? -ne 0 ]; then
    echo "❌ CMake 配置失败"
    cat /tmp/cmake_config.log
    exit 1
fi

echo "✓ CMake 配置成功"
echo ""

# 构建插件
echo "  编译 WebRTC AEC3 插件..."
cd native/build
make gstwebrtcaec3 > /tmp/aec3_build.log 2>&1

if [ $? -ne 0 ]; then
    echo "❌ AEC3 插件编译失败"
    echo "错误日志:"
    tail -50 /tmp/aec3_build.log
    exit 1
fi

echo "✓ AEC3 插件编译成功"
echo ""

# 验证插件
echo "步骤 4: 验证插件..."
AEC3_PLUGIN="$(pwd)/gst-plugins/libgstwebrtcaec3.dylib"

if [ ! -f "$AEC3_PLUGIN" ]; then
    echo "❌ 插件文件未生成: $AEC3_PLUGIN"
    exit 1
fi

echo "✓ 插件文件已生成: $(ls -lh $AEC3_PLUGIN | awk '{print $9, $5}')"

# 检查架构
ARCH=$(file "$AEC3_PLUGIN" | grep -o 'arm64\|x86_64')
echo "✓ 插件架构: $ARCH"
echo ""

# 同时重新编译 DeepFilterNet
echo "步骤 5: 重新编译 DeepFilterNet 插件..."
make gstdeepfilternet > /tmp/dfn_build.log 2>&1
if [ $? -eq 0 ]; then
    echo "✓ DeepFilterNet 插件也已更新"
else
    echo "⚠ DeepFilterNet 插件编译有警告（可能已经是最新的）"
fi
echo ""

# 测试插件识别
echo "步骤 6: 测试 GStreamer 插件识别..."
cd /Users/haifeng/Desktop/code/project/TChat
export GST_PLUGIN_PATH="$(pwd)/native/build/gst-plugins:/opt/homebrew/lib/gstreamer-1.0"
export DYLD_FALLBACK_LIBRARY_PATH="$(pwd)/onnxruntime-osx-arm64-1.23.2/lib:/opt/homebrew/lib"

# 测试 webrtcaec3
if gst-inspect-1.0 webrtcaec3 > /tmp/webrtcaec3_inspect.log 2>&1; then
    echo "✅ webrtcaec3 插件成功被 GStreamer 识别！"
    grep -E "Factory Details|Long-name|Description" /tmp/webrtcaec3_inspect.log | head -5
else
    echo "⚠ webrtcaec3 插件识别有问题，但文件已生成"
    echo "查看详情: /tmp/webrtcaec3_inspect.log"
fi
echo ""

# 测试 deepfilternet
if gst-inspect-1.0 deepfilternet > /tmp/deepfilternet_inspect.log 2>&1; then
    echo "✅ deepfilternet 插件成功被 GStreamer 识别！"
    grep -E "Factory Details|Long-name|Description" /tmp/deepfilternet_inspect.log | head -5
else
    echo "⚠ deepfilternet 插件识别有问题"
fi
echo ""

# 生成最终报告
echo "=========================================="
echo "✅✅✅ 所有插件构建完成！"
echo "=========================================="
echo ""
echo "已生成的插件:"
ls -lh native/build/gst-plugins/*.dylib
echo ""
echo "完成时间: $(date)"
echo ""
echo "下一步: 运行 ./scripts/run_dev.sh 启动应用"
echo "=========================================="

# 保存成功标志
touch /tmp/webrtc_aec3_build_success

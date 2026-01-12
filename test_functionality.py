#!/usr/bin/env python3
"""
TChat 功能测试脚本
测试 GStreamer、DeepFilterNet 插件和 VAD 功能
"""
import os
import sys

# 设置环境变量
if sys.platform == 'darwin':
    os.environ.setdefault('DYLD_FALLBACK_LIBRARY_PATH', '/opt/homebrew/lib')
    os.environ.setdefault('GI_TYPELIB_PATH', '/opt/homebrew/lib/girepository-1.0')

# 添加 ONNX Runtime 路径
project_root = os.path.dirname(os.path.abspath(__file__))
onnx_lib_path = os.path.join(project_root, 'onnxruntime-osx-arm64-1.23.2', 'lib')
if os.path.exists(onnx_lib_path):
    current_path = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
    os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = f"{onnx_lib_path}:{current_path}"

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

print("=" * 60)
print("TChat 功能测试")
print("=" * 60)
print()

# 初始化 GStreamer
Gst.init(['--gst-disable-registry-fork'])
print("✓ GStreamer 初始化成功")
print(f"  版本: {Gst.version_string()}")
print()

# 测试插件可用性
print("检查 GStreamer 插件...")
plugins_to_check = [
    ('deepfilternet', 'DeepFilterNet 降噪'),
    ('opusenc', 'Opus 编码'),
    ('opusdec', 'Opus 解码'),
    ('rtpopuspay', 'RTP Opus Payload'),
    ('rtpopusdepay', 'RTP Opus Depayload'),
    ('osxaudiosrc', 'macOS 音频源'),
    ('osxaudiosink', 'macOS 音频输出'),
]

for plugin_name, description in plugins_to_check:
    factory = Gst.ElementFactory.find(plugin_name)
    if factory:
        print(f"  ✓ {plugin_name:20s} - {description}")
    else:
        print(f"  ✗ {plugin_name:20s} - {description} (未找到)")

print()

# 测试 ONNX Runtime
print("检查 ONNX Runtime...")
try:
    import onnxruntime as ort
    print(f"  ✓ ONNX Runtime 版本: {ort.__version__}")
    providers = ort.get_available_providers()
    print(f"  ✓ 可用 Providers: {', '.join(providers)}")
except Exception as e:
    print(f"  ✗ ONNX Runtime 加载失败: {e}")

print()

# 测试 PySide6
print("检查 GUI 框架...")
try:
    from PySide6 import QtWidgets, QtCore
    print(f"  ✓ PySide6 版本: {QtCore.__version__}")
except Exception as e:
    print(f"  ✗ PySide6 加载失败: {e}")

print()

# 检查模型文件
print("检查模型文件...")
model_files = [
    ('models/silero_vad.onnx', 'Silero VAD 模型'),
    ('models/DeepFilterNet/enc.onnx', 'DeepFilterNet Encoder'),
    ('models/DeepFilterNet/erb_dec.onnx', 'DeepFilterNet ERB Decoder'),
    ('models/DeepFilterNet/df_dec.onnx', 'DeepFilterNet DF Decoder'),
    ('models/DeepFilterNet/config.ini', 'DeepFilterNet 配置'),
]

for file_path, description in model_files:
    full_path = os.path.join(project_root, file_path)
    if os.path.exists(full_path):
        size = os.path.getsize(full_path)
        size_str = f"{size/1024:.1f} KB" if size < 1024*1024 else f"{size/1024/1024:.1f} MB"
        print(f"  ✓ {description:30s} ({size_str})")
    else:
        print(f"  ⚠ {description:30s} (未找到)")

print()

# 测试创建 DeepFilterNet 元素
print("测试创建 DeepFilterNet 元素...")
try:
    dfn = Gst.ElementFactory.make('deepfilternet', 'test-dfn')
    if dfn:
        print("  ✓ DeepFilterNet 元素创建成功")
        
        # 设置模型路径
        model_dir = os.path.join(project_root, 'models/DeepFilterNet')
        if os.path.exists(model_dir):
            dfn.set_property('model-dir', model_dir)
            print(f"  ✓ 模型目录设置: {model_dir}")
        
        # 获取属性
        bypass = dfn.get_property('bypass')
        print(f"  ✓ Bypass 状态: {bypass}")
    else:
        print("  ✗ DeepFilterNet 元素创建失败")
except Exception as e:
    print(f"  ✗ 错误: {e}")
    import traceback
    traceback.print_exc()

print()

# 测试音频设备枚举
print("枚举音频设备...")
try:
    device_monitor = Gst.DeviceMonitor.new()
    device_monitor.add_filter("Audio/Source", None)
    device_monitor.add_filter("Audio/Sink", None)
    
    if device_monitor.start():
        devices = device_monitor.get_devices()
        print(f"  找到 {len(devices)} 个音频设备:")
        
        for i, device in enumerate(devices, 1):
            name = device.get_display_name()
            device_class = device.get_device_class()
            print(f"    {i}. {name} ({device_class})")
        
        device_monitor.stop()
    else:
        print("  ⚠ 无法启动设备监控器")
except Exception as e:
    print(f"  ✗ 设备枚举失败: {e}")

print()
print("=" * 60)
print("测试完成！")
print("=" * 60)
print()
print("如果所有测试都通过，可以运行:")
print("  ./scripts/run_dev.sh")
print()

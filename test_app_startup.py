#!/usr/bin/env python3
"""快速测试应用是否能启动并列出设备"""
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# GStreamer must be imported first
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

# Initialize GStreamer
Gst.init(['--gst-disable-registry-fork'])
print("✓ GStreamer initialized")

# Import PySide6
from PySide6.QtWidgets import QApplication
print("✓ PySide6 imported")

# Import app components
from app.metrics import Metrics
from app.vad import VADManager
from app.media import MediaEngine

print("\n测试设备列表功能...")
metrics = Metrics()
vad = VADManager(metrics, 'models/silero_vad.onnx')
media = MediaEngine(metrics, vad)

sources, sinks = media.list_devices()
print(f"\n✓ 找到 {len(sources)} 个输入设备:")
for dev in sources:
    print(f"  - {dev['name']} (ID: {dev['id']})")

print(f"\n✓ 找到 {len(sinks)} 个输出设备:")
for dev in sinks:
    print(f"  - {dev['name']} (ID: {dev['id']})")

print("\n✅ 所有测试通过!")

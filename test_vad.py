#!/usr/bin/env python3
"""Test VAD sink behavior"""
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import signal
import sys

Gst.init(None)

def on_sample(sink):
    print("✓ VAD SAMPLE RECEIVED!")
    sample = sink.emit("pull-sample")
    if sample:
        buf = sample.get_buffer()
        print(f"  Buffer size: {buf.get_size()} bytes")
    return Gst.FlowReturn.OK

def on_message(bus, message):
    t = message.type
    if t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        print(f"ERROR: {err}")
        loop.quit()
    elif t == Gst.MessageType.EOS:
        print("EOS")
        loop.quit()
    elif t == Gst.MessageType.STATE_CHANGED:
        if message.src.get_name() == "vad_sink":
            old, new, pending = message.parse_state_changed()
            print(f"VAD sink state: {old.value_nick} -> {new.value_nick}")
    return True

# Build minimal pipeline
pipeline = Gst.Pipeline.new("test")
src = Gst.ElementFactory.make("audiotestsrc", "src")
src.set_property("wave", 0)  # sine wave
src.set_property("freq", 440)

conv = Gst.ElementFactory.make("audioconvert", "conv")
res = Gst.ElementFactory.make("audioresample", "res")
caps = Gst.ElementFactory.make("capsfilter", "caps")
caps.set_property("caps", Gst.Caps.from_string("audio/x-raw,format=F32LE,rate=48000,channels=1"))

vad_sink = Gst.ElementFactory.make("appsink", "vad_sink")
vad_sink.set_property("emit-signals", True)
vad_sink.set_property("sync", False)
vad_sink.set_property("max-buffers", 1)
vad_sink.set_property("drop", True)
vad_sink.connect("new-sample", on_sample)

for elem in [src, conv, res, caps, vad_sink]:
    pipeline.add(elem)

Gst.Element.link_many(src, conv, res, caps, vad_sink)

bus = pipeline.get_bus()
bus.add_signal_watch()
bus.connect("message", on_message)

print("Starting pipeline...")
pipeline.set_state(Gst.State.PLAYING)

ret = pipeline.get_state(timeout=5 * Gst.SECOND)
print(f"Pipeline state: {ret[0].value_nick}, {ret[1].value_nick}")

vad_state = vad_sink.get_state(timeout=1 * Gst.SECOND)
print(f"VAD sink state: {vad_state[1].value_nick}")

loop = GLib.MainLoop()

def signal_handler(sig, frame):
    print("\nStopping...")
    loop.quit()

signal.signal(signal.SIGINT, signal_handler)

print("Running for 5 seconds... (Ctrl+C to stop)")
GLib.timeout_add_seconds(5, loop.quit)
loop.run()

pipeline.set_state(Gst.State.NULL)
print("Done")

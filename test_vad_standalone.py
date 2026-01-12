#!/usr/bin/env python3
import os
import sys
import numpy as np
import onnxruntime as ort

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "silero_vad.onnx")

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
CONTEXT_SIZE = 64
STATE_SHAPE = (2, 1, 128)

def test_model():
    sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    
    state = np.zeros(STATE_SHAPE, dtype=np.float32)
    context = np.zeros(CONTEXT_SIZE, dtype=np.float32)
    # 0-dim array (scalar tensor) for sr
    sr = np.array(SAMPLE_RATE, dtype=np.int64)
    
    print(f"sr type: {type(sr)}, shape: {sr.shape}, value: {sr}")
    
    silent_chunk = np.zeros(WINDOW_SIZE, dtype=np.float32)
    input_data = np.concatenate([context, silent_chunk]).reshape(1, -1).astype(np.float32)
    
    try:
        res = sess.run(None, {'input': input_data, 'state': state, 'sr': sr})
        prob = float(np.squeeze(res[0]))
        print(f"Silent: prob={prob:.4f} ✓")
    except Exception as e:
        print(f"ERROR: {e}")
        return False
    
    # Multi-frame test
    state = res[1]
    context = silent_chunk[-CONTEXT_SIZE:]
    
    for i in range(5):
        noise = np.random.randn(WINDOW_SIZE).astype(np.float32) * 0.1
        inp = np.concatenate([context, noise]).reshape(1, -1).astype(np.float32)
        res = sess.run(None, {'input': inp, 'state': state, 'sr': sr})
        prob = float(np.squeeze(res[0]))
        state = res[1]
        context = noise[-CONTEXT_SIZE:]
        print(f"Frame {i+1}: prob={prob:.4f}")
    
    print("\n=== ALL TESTS PASSED ===")
    return True

if __name__ == "__main__":
    success = test_model()
    sys.exit(0 if success else 1)

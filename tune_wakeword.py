"""
tune_wakeword.py
────────────────
Interactive threshold tuner for the Nube wake word model.
Run this to find the right WW_THRESHOLD value before setting it in nube.py.

Usage:
    python tune_wakeword.py

Press Ctrl+C to quit.
"""

import time
import numpy as np
import pyaudio
from openwakeword.model import Model

MODEL_PATH  = "Nube_wake_word.onnx"
SAMPLE_RATE = 16000
CHUNK_SIZE  = 1280   # 80ms — required by openWakeWord

print(f"Loading model: {MODEL_PATH}")
model = Model(wakeword_models=[MODEL_PATH])
print("Model loaded. Say 'Nube' and watch the score.\n")
print("  Score bar:  low = background noise   high = wake word detected")
print("  Pick a threshold just below where 'Nube' consistently peaks.\n")
print("Press Ctrl+C to quit.\n")

pa = pyaudio.PyAudio()
stream = pa.open(
    rate=SAMPLE_RATE,
    channels=1,
    format=pyaudio.paInt16,
    input=True,
    frames_per_buffer=CHUNK_SIZE,
)

peak = 0.0

try:
    while True:
        raw   = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        chunk = np.frombuffer(raw, dtype=np.int16)

        prediction = model.predict(chunk)
        score = max(prediction.values()) if prediction else 0.0

        if score > peak:
            peak = score

        bar    = "█" * int(score * 50)
        marker = "◀ PEAK {:.3f}".format(peak) if score == peak else ""
        print(f"\r  Score: {score:.4f}  |{bar:<50}| {marker}    ", end="", flush=True)

        # Print a newline when a high score is detected so you can see the history
        if score > 0.05:
            print(f"\n  *** {score:.4f} ***")

except KeyboardInterrupt:
    print(f"\n\nSession peak score: {peak:.4f}")
    print(f"\nSuggested WW_THRESHOLD: {max(peak * 0.7, 0.01):.3f}")
    print("Set this in nube.py:  WW_THRESHOLD = {:.3f}".format(max(peak * 0.7, 0.01)))
finally:
    stream.stop_stream()
    stream.close()
    pa.terminate()
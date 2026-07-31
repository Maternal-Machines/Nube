"""
Requirements:
    pip install pyserial sounddevice numpy keyboard openai pygame openwakeword pyaudio
"""

import argparse
import os
import sys
import tempfile
import time
import wave
import random
import threading

import numpy as np
import serial
import serial.tools.list_ports
import sounddevice as sd
import keyboard
import pygame

# -------------------------------------------------------------
#  OPENAI CLIENT
# -------------------------------------------------------------

try:
    from openai import OpenAI
    _key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")
    with open(_key_path, "r") as _f:
        _api_key = _f.read().strip()
    stt_client = OpenAI(api_key=_api_key)
    STT_BACKEND = "openai"
except FileNotFoundError:
    stt_client = None
    STT_BACKEND = "none"
    print("[WARN] api_key.txt not found — transcription disabled.")
except ImportError:
    stt_client = None
    STT_BACKEND = "none"
    print("[WARN] openai package not found — transcription disabled.")

# -------------------------------------------------------------
#  CONFIGURATION
# -------------------------------------------------------------

SERIAL_BAUD        = 9600
SAMPLE_RATE        = 16000
CHANNELS           = 1
ANSWERS_DIR        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "answers")
WAKEWORD_MODEL     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Nube_wake_word.onnx")
BLUETOOTH_SPEAKER  = "EWA Audio A106Pro"   # substring match on output device name
WW_THRESHOLD       = 0.05                   # tune if needed
WW_COOLDOWN_SECS   = 4
SILENCE_TIMEOUT    = 1.5                   # seconds of silence before recording stops

ANSWER_PAIRS = [
    # Q1 — can my diet affect how my baby will look?
    (
        ["diet", "baby look", "will look", "food", "appearance", "affect"],
        "a1.1.mp3", "a1.2.mp3",
    ),
    # Q2 — is there anything I can eat to make labour be smooth and quick?
    (
        ["labour", "labor", "smooth", "quick", "eat to make", "fast"],
        "a2.1.mp3", "a2.2.mp3",
    ),
    # Q3 — what are the symptoms of postpartum depression?
    (
        ["symptom", "postpartum depression", "postnatal depression", "depression",
         "feeling after", "after birth feel", "baby blues", "low mood"],
        "a3.1.mp3", "a3.2.mp3",
    ),
    # Q4 — what is postpartum care?
    (
        ["postpartum care", "postnatal care", "after birth care", "what is postpartum",
         "what is postnatal", "care after"],
        "a4.1.mp3", "a4.2.mp3",
    ),
    # Q5 — how long should my baby sleep?
    (
        ["how long", "baby sleep", "should sleep", "newborn sleep",
         "hours sleep", "nap", "sleep through"],
        "a5.1.mp3", "a5.2.mp3",
    ),
    # Q6 — umbilical cord?
    (
        ["umbilical cord", "nuchal cord", "wrapped around the baby's neck", "childbirth",
         "labour"],
        "a6.1.mp3", "a6.2.mp3",
    ),
    # Q7 — diabetes?
    (
        ["type one diabetes", "breastfeeding", "breast feeding", "breast-feeding",
         "insulin", "diabetes ", "feed"],
        "a7.1.mp3", "a7.2.mp3",
    ),
    # Q8 — should I have another baby?
    (
        ["have another baby", "wait for another baby", "another baby", "wait", "pregnant again", "conceive again"],
        "a8.1.mp3", "a8.2.mp3",
    ),
    # Q9 — how do I look after my baby boy?
    (
        ["baby boy", "newborn boy", "boys", "boys genitals", "boy nappy"],
        "a9.1.mp3", "a9.2.mp3",
    ),
    # Q10 — how do I look after my baby girl?
    (
        ["baby girl", "newborn girl", "girls", "girl nappy", "girls genitals"],
        "a10.1.mp3", "a10.2.mp3",
    ),
    # Q11 — what happens to my placenta if I have a caesarean?
    (
        ["c-section", "caesarean", "placenta"],
        "a11.1.mp3", "a11.2.mp3",
    ),
    # Q12 — where can a lesbian mother get support?
    (
        ["lesbian", "lesbian mother", "queer parent", "LGBT+ support", "support"],
        "a12.1.mp3", "a12.2.mp3",
    ),
    # Q13 — why is parenthood both lovely and difficult?
    (
        ["parenthood difficult and lovely", "parenthood difficult", "parenthood lovely"],
        "a13.1.mp3", "a13.2.mp3",
    ),
    # Q14 — should I sleep on the same bed with my baby?
    (
        ["co-sleep", "same bed", "sleep with baby", "safe sleep"],
        "a14.1.mp3", "a14.2.mp3",
    ),
    # Q15 — how long should my baby sleep? (duplicate)
    (
        ["how long", "baby sleep", "should sleep", "newborn sleep",
         "hours sleep", "nap", "sleep through"],
        "a15.1.mp3", "a15.2.mp3",
    ),
    # Fallback
    (["*"], lambda: random.choice(["unknown.mp3", "unknown2.mp3", "unknown3.mp3", "unknown4.mp3", "unknown5.mp3"]), None),
]

# -------------------------------------------------------------
#  AUDIO DEVICE HELPERS
# -------------------------------------------------------------

def find_input_device():
    """Find the default laptop microphone (first non-bluetooth input device)."""
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        name = d['name'].lower()
        if d['max_input_channels'] > 0:
            # Skip bluetooth devices for input
            if BLUETOOTH_SPEAKER.lower() not in name:
                return i
    return None  # fall back to system default


def find_output_device():
    """Find the Bluetooth speaker by name substring match."""
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if BLUETOOTH_SPEAKER.lower() in d['name'].lower() and d['max_output_channels'] > 0:
            print(f"[audio] Bluetooth speaker found: {d['name']} (device {i})")
            return i
    print(f"[WARN] Bluetooth speaker '{BLUETOOTH_SPEAKER}' not found — using system default output")
    return None


def set_pygame_output_device():
    """Point pygame audio output to the Bluetooth speaker if available."""
    device_idx = find_output_device()
    if device_idx is not None:
        device_info = sd.query_devices()[device_idx]
        # pygame uses the OS default; on Windows we can set it via device name
        # The most reliable cross-platform approach is to init pygame with the device name
        try:
            pygame.mixer.quit()
            pygame.mixer.init(devicename=device_info['name'])
            print(f"[audio] pygame output set to: {device_info['name']}")
        except Exception:
            # Older pygame versions don't support devicename — fall back to default
            pygame.mixer.quit()
            pygame.mixer.init()
            print("[audio] Could not set specific output device — using system default")
    else:
        pygame.mixer.init()


INPUT_DEVICE = None   # set after device scan in main()

# -------------------------------------------------------------
#  WAKE WORD
# -------------------------------------------------------------

def load_wakeword_model():
    try:
        from openwakeword.model import Model
        if not os.path.exists(WAKEWORD_MODEL):
            print(f"[WARN] Wake word model not found at {WAKEWORD_MODEL} — wake word disabled")
            return None
        model = Model(wakeword_model_paths=[WAKEWORD_MODEL])
        print(f"[wakeword] Model loaded: {WAKEWORD_MODEL}")
        return model
    except ImportError:
        print("[WARN] openwakeword not installed — wake word disabled. Use 'r' key instead.")
        return None


# -------------------------------------------------------------
#  ARDUINO SERIAL
# -------------------------------------------------------------

def find_arduino_port():
    candidates = list(serial.tools.list_ports.comports())
    for p in candidates:
        desc = (p.description or "").lower()
        mfr  = (p.manufacturer or "").lower()
        if any(x in desc or x in mfr for x in ["arduino", "ch340", "cp210", "ftdi"]):
            return p.device
    return candidates[0].device if candidates else None


def open_serial(port=None, baud=SERIAL_BAUD, timeout=5):
    port = port or find_arduino_port()
    if not port:
        sys.exit("[ERROR] No serial port found. Connect the Arduino or use --port.")
    print("[serial] Connecting to {} @ {} baud ...".format(port, baud))
    ser = serial.Serial(port, baud, timeout=1)
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode(errors="ignore").strip()
        if line == "READY":
            print("[serial] Arduino ready.")
            return ser
        if line:
            print("[serial] < {}".format(line))
    print("[WARN] Timed out waiting for READY — continuing anyway.")
    return ser


def send(ser, cmd):
    ser.write(cmd.encode())
    ser.flush()

# -------------------------------------------------------------
#  RECORDING — silence-based stop
# -------------------------------------------------------------

def record_until_silence(silence_timeout=SILENCE_TIMEOUT) -> np.ndarray | None:
    """
    Record from the laptop microphone until silence_timeout seconds of silence,
    or until 'r' is pressed again to stop manually.
    """
    frames = []
    silent_duration = 0.0
    chunk_duration = 0.05        # 50ms chunks
    chunk_samples = int(SAMPLE_RATE * chunk_duration)
    rms_threshold = 0.01         # tune if needed — lower = more sensitive to silence

    stop_event = threading.Event()

    def _key_stop():
        """Allow 'r' key to manually stop recording."""
        keyboard.wait('r')
        stop_event.set()

    key_thread = threading.Thread(target=_key_stop, daemon=True)
    key_thread.start()

    print("[record] Listening... (speak your question, or press 'r' to stop)")

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        device=INPUT_DEVICE,
        blocksize=chunk_samples,
    ) as stream:
        while not stop_event.is_set():
            chunk, _ = stream.read(chunk_samples)
            frames.append(chunk.copy())
            rms = np.sqrt(np.mean(chunk ** 2))
            if rms < rms_threshold:
                silent_duration += chunk_duration
                if silent_duration >= silence_timeout:
                    print(f"[record] Silence detected — stopping.")
                    break
            else:
                silent_duration = 0.0

    if not frames:
        return None

    audio = np.concatenate(frames, axis=0)
    duration = len(audio) / SAMPLE_RATE
    print(f"[record] Recorded {duration:.1f}s")
    if duration < 0.3:
        print("[record] Too short — ignoring")
        return None
    return audio

# -------------------------------------------------------------
#  TRANSCRIPTION
# -------------------------------------------------------------

def transcribe(audio):
    if STT_BACKEND == "none" or audio is None:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    try:
        with wave.open(tmp_path, "w") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())
        with open(tmp_path, "rb") as f:
            result = stt_client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="en",
            )
        return result.text.strip().lower()
    finally:
        os.unlink(tmp_path)

# -------------------------------------------------------------
#  ANSWER MATCHING
# -------------------------------------------------------------

def match_answer(text):
    text = text.lower()
    matched_p1, matched_p2 = None, None
    for keywords, p1, p2 in ANSWER_PAIRS:
        if keywords == ["*"]:
            if matched_p1 is None:
                matched_p1, matched_p2 = p1, p2
            break
        if any(kw in text for kw in keywords):
            matched_p1, matched_p2 = p1, p2
            break

    def resolve(fn):
        if not fn:
            return None
        if callable(fn):
            return fn
        path = os.path.join(ANSWERS_DIR, fn)
        if os.path.exists(path):
            return path
        print("[WARN] Audio file not found: {}".format(path))
        return None

    return resolve(matched_p1), resolve(matched_p2)

# -------------------------------------------------------------
#  PLAYBACK
# -------------------------------------------------------------

def play_mp3(path):
    pygame.mixer.music.load(path)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        time.sleep(0.05)

# -------------------------------------------------------------
#  ANSWER SEQUENCE
# -------------------------------------------------------------

def play_answer(ser, text):
    part1, part2 = match_answer(text)
    if not part1 and not part2:
        print("[controller] No audio found — returning to IDLE.")
        send(ser, 'I')
        return

    if part1:
        if callable(part1):
            part1 = os.path.join(ANSWERS_DIR, part1())
        print("[controller] Playing part 1: {}".format(os.path.basename(part1)))
        send(ser, 'P')
        time.sleep(0.1)
        play_mp3(part1)

    if part2:
        if callable(part2):
            part2 = part2()
        print("[controller] Playing part 2: {}".format(os.path.basename(part2)))
        send(ser, 'Q')
        time.sleep(0.05)
        play_mp3(part2)

    time.sleep(0.8)
    print("[controller] Done — returning to IDLE.")
    send(ser, 'D')

# -------------------------------------------------------------
#  HANDLE ONE INTERACTION
# -------------------------------------------------------------

def handle_interaction(ser):
    """Open eyes, record, transcribe, play answer."""
    send(ser, 'O')
    time.sleep(0.1)

    audio = record_until_silence()
    text = transcribe(audio)
    print('[controller] Heard: "{}"'.format(text))

    play_answer(ser, text)

# -------------------------------------------------------------
#  MAIN LOOP
# -------------------------------------------------------------

def run(ser, ww_model, silence_timeout):
    global SILENCE_TIMEOUT
    SILENCE_TIMEOUT = silence_timeout

    send(ser, 'I')

    print("\n[controller] Ready.")
    print("             Say 'Nube' to start, or press 'R' as backup.")
    print("             Recording stops after silence or press 'R' again.")
    print("             Press ESC to quit.\n")

    import pyaudio
    import ctypes

    # Suppress ALSA/PyAudio warnings on Linux
    try:
        asound = ctypes.cdll.LoadLibrary('libasound.so.2')
        ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int,
                                               ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
        asound.snd_lib_error_set_handler(ERROR_HANDLER_FUNC(lambda *a: None))
    except Exception:
        pass

    # ── Wake word listener thread ──────────────────────────────────────────────
    ww_triggered = threading.Event()
    ww_stop = threading.Event()
    last_detection = [0.0]

    def _ww_listen():
        if ww_model is None:
            return
        pa = pyaudio.PyAudio()
        stream = pa.open(
            rate=SAMPLE_RATE,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            input_device_index=INPUT_DEVICE,
            frames_per_buffer=1280,
        )
        print("[wakeword] Listening for 'Nube'...")
        try:
            while not ww_stop.is_set():
                raw = stream.read(1280, exception_on_overflow=False)
                chunk = np.frombuffer(raw, dtype=np.int16)
                prediction = ww_model.predict(chunk)
                score = max(prediction.values()) if prediction else 0.0
                now = time.time()
                if score >= WW_THRESHOLD and (now - last_detection[0]) > WW_COOLDOWN_SECS:
                    print(f"\n[wakeword] 'Nube' detected (score: {score:.3f})")
                    last_detection[0] = now
                    ww_triggered.set()
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    ww_thread = threading.Thread(target=_ww_listen, daemon=True)
    ww_thread.start()
    # ──────────────────────────────────────────────────────────────────────────

    try:
        while True:
            if keyboard.is_pressed("esc"):
                print("[controller] ESC — exiting.")
                break

            # Wake word trigger
            if ww_triggered.is_set():
                ww_triggered.clear()
                handle_interaction(ser)
                last_detection[0] = time.time()  # reset cooldown after interaction
                continue

            # 'R' key backup trigger
            if keyboard.is_pressed("r"):
                while keyboard.is_pressed("r"):  # wait for release
                    time.sleep(0.02)
                print("[controller] 'R' key pressed — starting interaction")
                handle_interaction(ser)
                continue

            time.sleep(0.02)

    except KeyboardInterrupt:
        pass
    finally:
        ww_stop.set()
        send(ser, 'I')
        ser.close()
        print("[controller] Goodbye.")

# -------------------------------------------------------------
#  ENTRY POINT
# -------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NUBE animatronic controller")
    parser.add_argument("--port", default=None, help="Serial port e.g. COM3")
    parser.add_argument("--silence", type=float, default=1.0,
                        help="Silence timeout in seconds before recording stops (default: 1.0)")
    args = parser.parse_args()

    # Scan audio devices
    INPUT_DEVICE = find_input_device()
    print(f"[audio] Input device: {sd.query_devices(INPUT_DEVICE)['name'] if INPUT_DEVICE is not None else 'system default'}")

    # Init pygame with bluetooth speaker
    set_pygame_output_device()

    # Load wake word model
    ww_model = load_wakeword_model()

    # Connect to Arduino
    ser = open_serial(port=args.port)

    run(ser, ww_model, args.silence)
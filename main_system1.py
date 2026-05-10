import serial
import threading
import subprocess
import time
import os
import cv2
import numpy as np
import pyaudio
import librosa
import warnings
warnings.filterwarnings('ignore')

# Force OpenCV to use XCB (X11) — avoids Qt Wayland plugin warning on Pi
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')

try:
    import tensorflow as tf
    Interpreter = tf.lite.Interpreter
    print("[INFO] Using tensorflow interpreter (Flex ops supported)")
except ImportError:
    try:
        from ai_edge_litert.interpreter import Interpreter
        print("[INFO] Using ai_edge_litert")
    except ImportError:
        from tflite_runtime.interpreter import Interpreter
        print("[INFO] Using tflite_runtime")
from collections import deque, Counter
import mediapipe as mp

# ── Arduino Serial ────────────────────────────────────────
arduino = None
for port in ['/dev/ttyUSB0', '/dev/ttyACM0', '/dev/ttyUSB1']:
    try:
        arduino = serial.Serial(port, 9600, timeout=1)
        time.sleep(2)
        print(f"Arduino Connected on {port}!")
        break
    except:
        continue
if arduino is None:
    print("Arduino NOT found — running without Arduino")

# ── Shared active command state ───────────────────────────
# Holds the CURRENT latched command (closed-loop).
# Both voice and gesture threads write here; the sender
# thread continuously pushes it to Arduino so motors keep
# running until told otherwise.
active_command      = 'S'          # start stopped
active_command_lock = threading.Lock()

def set_active_command(cmd):
    """Latch a new command. The sender thread will keep pushing it."""
    global active_command
    with active_command_lock:
        if active_command != cmd:
            active_command = cmd
            print(f"[CMD] Active command → {cmd}")

def get_active_command():
    with active_command_lock:
        return active_command

# ── Continuous sender thread ──────────────────────────────
# Sends the active command to Arduino every SEND_INTERVAL seconds.
# This keeps the motors running — the Arduino just needs a heartbeat.
SEND_INTERVAL = 0.15   # seconds — tune to match your Arduino sketch

def continuous_sender_thread():
    """Keeps sending the current active command to Arduino in a loop."""
    while True:
        cmd = get_active_command()
        if arduino is not None:
            try:
                arduino.write(cmd.encode())
            except Exception as e:
                print(f"[SENDER ERROR] {e}")
        time.sleep(SEND_INTERVAL)

def label_to_command(label):
    label = label.upper()
    mapping = {
        'FORWARD'   : 'F',
        'BACKWARD'  : 'B',
        'LEFT'      : 'L',
        'RIGHT'     : 'R',
        'STOP'      : 'S',
        'BACKGROUND': None,
        'SILENCE'   : None,
        'NOISE'     : None
    }
    return mapping.get(label, None)

# ─────────────────────────────────────────────────────────
# VOICE CONTROL
# ─────────────────────────────────────────────────────────
VOICE_MODEL_PATH = '/home/pi/wheelchair_project/ds_cnn_kws_voice_last.tflite'
VOICE_LABELS     = ['Backward', 'Forward', 'Left',
                    'Right', 'Stop', 'Background']

# ── Tunable voice parameters ──────────────────────────────
# Minimum confidence to accept a prediction.
# Real speech typically hits 0.85-1.00; noise sits 0.39-0.75.
VOICE_THRESHOLD = 0.55

# Frames below this volume are skipped (no inference).
# Real speech vol=1363-6620, ambient noise vol=134-742.
# Lower to 600 if real commands are being skipped.
MIN_VOLUME = 1000

# After firing a command, ignore the SAME command for this long.
# Prevents one long utterance from re-triggering repeatedly.
# A DIFFERENT command always fires immediately regardless.
VOICE_COOLDOWN = 2.5   # seconds

MIC_RATE   = 44100
MODEL_RATE = 16000

def preprocess_audio(audio_data):
    # Step 1 — convert to float
    audio_float = audio_data.astype(np.float32) / 32768.0

    # Step 2 — resample to 16000 Hz
    audio_resampled = librosa.resample(
        audio_float,
        orig_sr   = MIC_RATE,
        target_sr = MODEL_RATE
    )

    # Step 3 — fix length to exactly 16000 samples
    if len(audio_resampled) < MODEL_RATE:
        audio_resampled = np.pad(
            audio_resampled,
            (0, MODEL_RATE - len(audio_resampled))
        )
    else:
        audio_resampled = audio_resampled[:MODEL_RATE]

    # Step 4 — normalize
    audio_resampled = audio_resampled / \
                      (np.max(np.abs(audio_resampled)) + 1e-6)

    # Step 5 — Mel spectrogram
    mel = librosa.feature.melspectrogram(
        y          = audio_resampled,
        sr         = MODEL_RATE,
        n_mels     = 40,
        n_fft      = 1024,
        hop_length = 512
    )

    # Step 6 — convert to dB
    log_mel = librosa.power_to_db(mel)

    # Step 7 — fix to exactly (40, 32)
    if log_mel.shape[1] < 32:
        log_mel = np.pad(
            log_mel,
            ((0, 0), (0, 32 - log_mel.shape[1])))
    else:
        log_mel = log_mel[:, :32]

    # Step 8 — reshape to (1, 40, 32, 1)
    return log_mel.reshape(1, 40, 32, 1).astype(np.float32)

def voice_control_thread():
    print("Loading voice model...")
    voice_interp = Interpreter(model_path=VOICE_MODEL_PATH)
    voice_interp.allocate_tensors()
    v_in  = voice_interp.get_input_details()
    v_out = voice_interp.get_output_details()
    print("Voice model loaded! ✅")

    pa     = pyaudio.PyAudio()
    stream = pa.open(
        format             = pyaudio.paInt16,
        channels           = 1,
        rate               = MIC_RATE,
        input              = True,
        input_device_index = 0,
        frames_per_buffer  = 1024
    )
    print(f"Voice ready — threshold={VOICE_THRESHOLD}  "
          f"vol_gate={MIN_VOLUME}  cooldown={VOICE_COOLDOWN}s")
    print("Say: Forward, Backward, Left, Right, Stop")

    # Per-command last-fired timestamp.
    # Prevents the SAME command re-triggering during a long utterance,
    # while a DIFFERENT command always fires immediately.
    last_fired = {}   # cmd -> timestamp

    while True:
        try:
            # Capture 1 second of audio
            frames = []
            for _ in range(int(MIC_RATE * 1.0 / 1024)):
                data = stream.read(1024, exception_on_overflow=False)
                frames.append(np.frombuffer(data, dtype=np.int16))
            audio_data = np.concatenate(frames)

            volume = np.abs(audio_data).mean()

            # Volume gate — skip inference on quiet frames.
            # Does NOT change active command — wheelchair keeps moving.
            if volume < MIN_VOLUME:
                continue

            # Run model
            input_tensor = preprocess_audio(audio_data)
            voice_interp.set_tensor(v_in[0]['index'], input_tensor)
            voice_interp.invoke()
            output = voice_interp.get_tensor(v_out[0]['index'])[0]

            pred_idx   = np.argmax(output)
            confidence = float(output[pred_idx])
            label      = VOICE_LABELS[pred_idx]

            # Confidence gate
            if confidence < VOICE_THRESHOLD:
                print(f"[VOICE] {label} ({confidence:.2f}) "
                      f"vol={volume:.0f} — below threshold, ignored")
                continue

            # Ignore Background / non-command labels
            cmd = label_to_command(label)
            if cmd is None:
                print(f"[VOICE] {label} ({confidence:.2f}) "
                      f"vol={volume:.0f} — not a command")
                continue

            # Per-command cooldown
            now        = time.time()
            since_last = now - last_fired.get(cmd, 0)
            if since_last < VOICE_COOLDOWN:
                print(f"[VOICE] {label} ({confidence:.2f}) vol={volume:.0f}"
                      f" — cooldown ({since_last:.1f}s/{VOICE_COOLDOWN}s)")
                continue

            # Fire!
            last_fired[cmd] = now
            set_active_command(cmd)
            print(f"[VOICE] ✅ FIRED → {cmd}  "
                  f"({label} {confidence:.2f}  vol={volume:.0f})")

        except Exception as e:
            print(f"[VOICE ERROR] {e}")
            time.sleep(0.5)

# ─────────────────────────────────────────────────────────
# GESTURE CONTROL
# ─────────────────────────────────────────────────────────
GESTURE_MODEL_PATH = '/home/pi/wheelchair_project/gesture_model_gru.tflite'

GESTURE_LABELS    = ['LEFT', 'RIGHT', 'FORWARD', 'BACKWARD', 'STOP']
GESTURE_THRESHOLD      = 0.25   # model peaks at 0.34 in deployment
                                # (training used cv2 camera, deployment
                                #  uses rpicam-vid — slight pixel difference)
PREDICTION_BUFFER_SIZE = 3      # reduced from 5 — faster response
NUM_FRAMES        = 30
NUM_LANDMARKS     = 42

last_gesture_time      = 0
GESTURE_COOLDOWN       = 2.0

mp_hands = mp.solutions.hands
mp_draw  = mp.solutions.drawing_utils
hands    = mp_hands.Hands(
    max_num_hands            = 1,
    min_detection_confidence = 0.7,
    min_tracking_confidence  = 0.7
)

# How many consecutive no-hand frames before sending Stop (~1s at 30fps)
# Increased from 15 — prevents brief occlusions killing the gesture
NO_HAND_STOP_FRAMES = 30

def extract_landmarks(hand_landmarks):
    landmarks = []
    for lm in hand_landmarks.landmark:
        landmarks.extend([lm.x, lm.y])
    return landmarks

def gesture_control_thread():
    global last_gesture_time

    print("Loading gesture model...")
    gesture_interp = Interpreter(
        model_path      = GESTURE_MODEL_PATH,
        experimental_delegates = []   # Flex ops are built into ai_edge_litert
    )
    gesture_interp.allocate_tensors()
    g_in  = gesture_interp.get_input_details()
    g_out = gesture_interp.get_output_details()
    print(f"  Input : {g_in[0]['shape']}")
    print(f"  Output: {g_out[0]['shape']}")
    print("Gesture model loaded! ✅")

    # ── Pi Camera via rpicam-vid stdout pipe ──────────────
    # rpicam-vid is a system binary — works with any Python version.
    # Streams raw YUV420 frames to stdout; we read, convert, process.
    # No libcamera Python bindings or picamera2 needed at all.
    CAM_W, CAM_H = 640, 480
    # YUV420 size: W×H (Y plane) + W×H÷2 (UV planes)
    YUV_SIZE = CAM_W * CAM_H * 3 // 2

    cam_cmd = [
        'rpicam-vid',
        '--width',     str(CAM_W),
        '--height',    str(CAM_H),
        '--framerate', '30',
        '--codec',     'yuv420',   # raw YUV420 output
        '--output',    '-',        # write frames to stdout
        '--timeout',   '0',        # run forever until killed
        '--nopreview',             # no separate preview window
    ]

    print("[GESTURE] Starting rpicam-vid pipe...")
    cam_proc = subprocess.Popen(
        cam_cmd,
        stdout = subprocess.PIPE,
        stderr = subprocess.DEVNULL,
        bufsize = YUV_SIZE * 2     # buffer 2 frames ahead
    )
    print(f"[GESTURE] rpicam-vid started ✅  ({CAM_W}x{CAM_H} YUV420 @ 30fps)")

    sequence_buffer   = []
    prediction_buffer = deque(maxlen=PREDICTION_BUFFER_SIZE)
    label             = "-"
    confidence        = 0.0
    final_command     = ""
    color             = (0, 165, 255)
    no_hand_counter   = 0
    hand_ever_detected = False   # only send Stop after hand was seen first

    print("Gesture ready — show hand to camera")

    while True:
        # ── Read one raw YUV420 frame from the pipe ───────
        raw = cam_proc.stdout.read(YUV_SIZE)
        if len(raw) < YUV_SIZE:
            print("[GESTURE] Camera pipe closed — stopping")
            break

        # ── YUV420 → BGR (for OpenCV display) ─────────────
        yuv = np.frombuffer(raw, dtype=np.uint8).reshape(
            (CAM_H * 3 // 2, CAM_W))
        frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

        # Flip horizontally — mirrors like a selfie camera
        frame = cv2.flip(frame, 1)

        # ── BGR → RGB for MediaPipe ────────────────────────
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result    = hands.process(frame_rgb)

        if result.multi_hand_landmarks:
            no_hand_counter    = 0   # hand visible — reset counter
            hand_ever_detected = True

            hand_lm = result.multi_hand_landmarks[0]
            mp_draw.draw_landmarks(
                frame, hand_lm, mp_hands.HAND_CONNECTIONS)

            landmarks = extract_landmarks(hand_lm)
            sequence_buffer.append(landmarks)

            if len(sequence_buffer) == NUM_FRAMES:

                input_data = np.expand_dims(
                    sequence_buffer, axis=0
                ).astype(np.float32)   # shape (1, 30, 42)

                gesture_interp.set_tensor(
                    g_in[0]['index'], input_data)
                gesture_interp.invoke()
                output = gesture_interp.get_tensor(
                    g_out[0]['index'])[0]

                pred_idx   = np.argmax(output)
                confidence = float(output[pred_idx])
                label      = GESTURE_LABELS[pred_idx]

                # Show ALL class scores so you can see what model thinks
                scores_str = " | ".join(
                    f"{GESTURE_LABELS[i]}={output[i]:.2f}"
                    for i in range(len(GESTURE_LABELS))
                )
                print(f"[GESTURE] {label} ({confidence:.2f})  [{scores_str}]")

                if confidence >= GESTURE_THRESHOLD:
                    prediction_buffer.append(pred_idx)
                    print(f"[GESTURE] ✅ Added to vote buffer "
                          f"({len(prediction_buffer)}/{PREDICTION_BUFFER_SIZE})")
                else:
                    print(f"[GESTURE] ❌ Below threshold "
                          f"({confidence:.2f} < {GESTURE_THRESHOLD}) — not counted")

                # Majority vote across buffer
                if len(prediction_buffer) == PREDICTION_BUFFER_SIZE:
                    most_common = Counter(
                        prediction_buffer).most_common(1)[0][0]
                    final_command = GESTURE_LABELS[most_common]
                    cmd = label_to_command(final_command)

                    if cmd:
                        # Only print if command actually changes
                        # (sender thread keeps it running, no need to re-fire)
                        if cmd != get_active_command():
                            set_active_command(cmd)
                            last_gesture_time = time.time()
                            print(f"[GESTURE] ✅ FIRED → Arduino '{cmd}'")
                            color = (0, 255, 0)
                    prediction_buffer.clear()
                else:
                    color = (0, 165, 255)

                # Slide buffer by 1 frame
                sequence_buffer.pop(0)

        else:
            # No hand visible
            no_hand_counter += 1
            sequence_buffer.clear()
            prediction_buffer.clear()
            label         = "No Hand"
            confidence    = 0.0
            color         = (0, 0, 255)
            final_command = ""

            # Stop only after enough consecutive no-hand frames
            # Use == not >= so it only fires ONCE, not every frame
            if no_hand_counter == NO_HAND_STOP_FRAMES and hand_ever_detected:
                set_active_command('S')
                print("[GESTURE] Hand lost → Stop")

        # ── Display overlay ────────────────────────────────
        current_cmd = get_active_command()

        cv2.putText(frame, f"Gesture: {label}",
                    (10, 40), cv2.FONT_HERSHEY_SIMPLEX,
                    1, color, 2)
        cv2.putText(frame, f"Conf: {confidence:.2f}",
                    (10, 80), cv2.FONT_HERSHEY_SIMPLEX,
                    1, color, 2)
        cv2.putText(frame,
                    f"Buffer: {len(sequence_buffer)}/{NUM_FRAMES}",
                    (10, 120), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 1)
        cv2.putText(frame,
                    f"Vote: {len(prediction_buffer)}/{PREDICTION_BUFFER_SIZE}",
                    (10, 150), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 0), 1)
        cv2.putText(frame,
                    f"ACTIVE CMD: {current_cmd}",
                    (10, 185), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (0, 0, 255), 2)
        cv2.putText(frame,
                    f"Arduino: {'OK' if arduino else 'NOT FOUND'}",
                    (10, 220), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0) if arduino else (0, 0, 255), 2)

        guide = ["0=LEFT", "1=RIGHT", "2=FORWARD",
                 "3=BACKWARD", "4=STOP"]
        for i, g in enumerate(guide):
            cv2.putText(frame, g, (10, 255 + i * 22),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 0), 1)

        cv2.imshow("Gesture Control", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            set_active_command('S')   # safety stop on quit
            break

    # ── Cleanup ───────────────────────────────────────────
    cam_proc.terminate()
    cam_proc.wait()
    cv2.destroyAllWindows()

# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("   Wheelchair AI Control System")
    print("   Voice + Gesture → Arduino  (Closed-Loop)")
    print("=" * 50)
    print("VOICE  : Forward, Backward, Left, Right, Stop")
    print("GESTURE: LEFT, RIGHT, FORWARD, BACKWARD, STOP")
    print("Commands LATCH — wheelchair keeps moving until")
    print("you give a new command.")
    print("Camera : rpicam-vid pipe (no libcamera/picamera2 needed)")
    print("Press Q on camera window to quit")
    print("=" * 50)

    # Start the continuous Arduino sender thread
    sender_thread = threading.Thread(
        target=continuous_sender_thread, daemon=True)
    sender_thread.start()

    # Start the voice recognition thread
    voice_thread = threading.Thread(
        target=voice_control_thread, daemon=True)
    voice_thread.start()

    # Gesture runs on the main thread (needs the OpenCV window)
    gesture_control_thread()

    # Safety stop when quitting
    set_active_command('S')
    if arduino:
        arduino.write(b'S')
        arduino.close()
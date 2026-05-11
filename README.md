# ♿ Solar-Powered Smart Wheelchair with AI Voice & Gesture Control

![Python](https://img.shields.io/badge/Python-3.x-blue)
![TensorFlow](https://img.shields.io/badge/TensorFlow-Lite-orange)
![Arduino](https://img.shields.io/badge/Arduino-Uno-teal)
![Raspberry Pi](https://img.shields.io/badge/Raspberry_Pi-4-red)
![Status](https://img.shields.io/badge/Status-Complete-brightgreen)

## 📌 Overview
An intelligent solar-powered wheelchair that uses AI to allow
hands-free control through voice commands and hand gestures.
Built and deployed on Raspberry Pi with real-time obstacle detection.

## 🎯 Features
- 🎙️ **Voice Control** — say Forward, Backward, Left, Right, Stop
- 🖐️ **Gesture Control** — hand gestures via Pi Camera + MediaPipe
- 🚧 **Obstacle Detection** — auto-stops using TF-Luna LiDAR + ultrasonic sensors
- ☀️ **Solar Powered** — sustainable energy for extended use
- 🔒 **Thread-Safe** — voice and gesture run simultaneously without conflict
- 🛑 **Safety First** — auto-stop on hand loss, timeout, or disconnection

## 🛠️ Tech Stack
| Layer | Technology |
|---|---|
| Language | Python, C++ (Arduino) |
| AI Models | TensorFlow Lite, Keras |
| Computer Vision | OpenCV, MediaPipe |
| Audio | Librosa, PyAudio |
| Hardware | Raspberry Pi 4, Arduino Uno |
| Sensors | TF-Luna LiDAR, HC-SR04 Ultrasonic x3 |
| Motors | Cytron Motor Driver |
| Power | Solar Panel Integration |

## 📁 Project Structure
solar-wheelchair-ai/
├── main_system1.py              # Master controller (runs on Raspberry Pi)
├── requirements.txt             # Python dependencies
├── voice_control/
│   ├── voice_control_model.ipynb  # Voice model training notebook
│   ├── voice_modelmodified.h5     # Trained Keras model
│   └── voice_modelmodified.tflite # Deployed TFLite model
├── gesture_control/
│   ├── gesture_model_smartwheelchair.ipynb  # Gesture model training
│   └── gesture_model_gru.tflite             # Deployed TFLite model
└── obstacle_detection/
└── Arduino_code.ino         # Arduino motor + sensor controller

## ⚙️ How It Works
1. **Voice thread** captures mic audio → Mel spectrogram → TFLite model → command
2. **Gesture thread** reads Pi Camera → MediaPipe landmarks → GRU model → command
3. **Sender thread** continuously sends the active command to Arduino every 150ms
4. **Arduino** receives command → checks sensors → drives motors safely

## 🚀 How to Run

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Upload Arduino code
- Open `obstacle_detection/Arduino_code.ino` in Arduino IDE
- Upload to Arduino Uno

### 3. Connect hardware
- Arduino via USB (`/dev/ttyUSB0` or `/dev/ttyACM0`)
- Raspberry Pi Camera module
- USB Microphone

### 4. Run the system
```bash
python main_system1.py
```

## 🧠 AI Models

### Voice Control
- **Input:** 1 second audio → Mel Spectrogram (40×32)
- **Architecture:** CNN
- **Commands:** Forward, Backward, Left, Right, Stop
- **Deployed as:** `.tflite` on Raspberry Pi

### Gesture Control
- **Input:** 30 frames of hand landmarks (42 points each)
- **Architecture:** Conv1D + GRU
- **Commands:** LEFT, RIGHT, FORWARD, BACKWARD, STOP
- **Deployed as:** `.tflite` on Raspberry Pi

## 🔧 Hardware Wiring
| Component | Pin |
|---|---|
| TF-Luna LiDAR | TX→12, RX→13 (SoftwareSerial) |
| Ultrasonic Front | Trig→2, Echo→3 |
| Ultrasonic Left | Trig→4, Echo→7 |
| Ultrasonic Back | Trig→10, Echo→11 |
| Motor 1 | PWM→5, DIR→8 |
| Motor 2 | PWM→6, DIR→9 |
| Joystick | VRX→A0, VRY→A1 |

## 📄 Publication
This project is part of ongoing research submitted to IEEE.

## 👤 Author
**Achuoth Akol Achuoth Deng**
B.Tech EEE — Amrita Vishwa Vidyapeetham, India
[LinkedIn](https://linkedin.com/in/achuoth-akol-achuoth-deng) · [GitHub](https://github.com/Achuoth11)

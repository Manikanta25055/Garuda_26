# 🤖 Hand-Controlled Robot - Quick Start

Control a servo motor with your hand movements using Hailo AI pose estimation!

## ⚡ Quick Start (5 Minutes)

### 1. Hardware Setup
Connect your hardware:
```
Servo:
  Brown/Black → GND (Pin 6)
  Red → 5V (Pin 2)
  Orange/Yellow → GPIO 17 (Pin 11)

LED + 220Ω Resistor:
  Anode (+) → GPIO 20 (Pin 38)
  Cathode (-) → GND (Pin 39)
```

### 2. Software Setup
```bash
cd /home/manikanta/Projects/hailo-rpi5-examples/basic_pipelines
source ../setup_env.sh
```

### 3. Test Hardware
```bash
python test_hardware.py
```

### 4. Run the Robot!
```bash
python hand_controlled_servo.py -i /dev/video0 --use-frame --show-fps
```

### 5. Control It
- Stand in front of camera
- Raise your hand
- Move hand left/right
- Watch servo follow!

---

## 📁 Project Files

| File | Purpose |
|------|---------|
| `hand_controlled_servo.py` | Main application - run this! |
| `test_hardware.py` | Test servo and LED before running |
| `HAND_ROBOT_GUIDE.md` | Complete guide with wiring, troubleshooting, advanced usage |
| `PIPELINE_LEARNING_GUIDE.md` | Deep dive into pipeline architecture |
| `QUICK_REFERENCE.md` | Command reference and code examples |

---

## 🎮 Command Examples

### Basic
```bash
# USB camera
python hand_controlled_servo.py -i /dev/video0

# RPi camera
python hand_controlled_servo.py -i rpi

# Show FPS
python hand_controlled_servo.py -i /dev/video0 --show-fps

# Show visualization
python hand_controlled_servo.py -i /dev/video0 --use-frame
```

### Advanced
```bash
# Faster servo movement
python hand_controlled_servo.py -i /dev/video0 --servo-speed 0.15

# More stable (larger deadzone)
python hand_controlled_servo.py -i /dev/video0 --deadzone 0.25

# Higher sensitivity
python hand_controlled_servo.py -i /dev/video0 --sensitivity 1.5

# Use better PWM (requires: sudo pigpiod)
python hand_controlled_servo.py -i /dev/video0 --use-pigpio

# Everything!
python hand_controlled_servo.py -i /dev/video0 \
  --show-fps --use-frame --use-pigpio \
  --servo-speed 0.1 --sensitivity 1.3
```

---

## 🔧 Parameters Explained

| Parameter | Default | Range | Effect |
|-----------|---------|-------|--------|
| `--servo-speed` | 0.08 | 0.01-0.5 | Movement speed (higher = faster) |
| `--deadzone` | 0.15 | 0.0-0.5 | Center zone with no movement (higher = more stable) |
| `--sensitivity` | 1.2 | 0.5-2.0 | How much servo moves (higher = more movement) |
| `--servo-pin` | 17 | Any GPIO | GPIO pin for servo PWM |
| `--led-pin` | 20 | Any GPIO | GPIO pin for LED |

---

## ❗ Troubleshooting

### Servo jitters
```bash
python hand_controlled_servo.py -i /dev/video0 --deadzone 0.25 --servo-speed 0.05
```

### Servo doesn't move
```bash
# Test servo
python test_hardware.py --skip-led --skip-camera

# Try pigpio
sudo pigpiod
python hand_controlled_servo.py -i /dev/video0 --use-pigpio
```

### Hands not detected
```bash
# Test camera and pose detection
python pose_estimation.py -i /dev/video0 --use-frame
```

### "Failed to import hailo"
```bash
source ../setup_env.sh
python -c "import hailo; print('OK')"
```

---

## 🎯 How It Works

```
Camera → Hailo AI Pose Detection → Extract Wrist Keypoints →
Calculate Hand Position → Map to Servo Angle → Move Servo
```

**Technical Details:**
- Uses YOLOv8 Pose model (17 COCO keypoints)
- Tracks wrist keypoints (indices 9 and 10)
- Runs at ~20 FPS on Raspberry Pi 5
- Smooth servo interpolation prevents jitter
- Deadzone in center prevents small movements

---

## 📚 Documentation

- **[HAND_ROBOT_GUIDE.md](HAND_ROBOT_GUIDE.md)** - Complete setup guide with wiring diagrams, troubleshooting, advanced usage
- **[PIPELINE_LEARNING_GUIDE.md](PIPELINE_LEARNING_GUIDE.md)** - Understanding the pipeline architecture
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Command reference and code snippets

---

## 🚀 Next Steps

### Beginner
1. Get basic tracking working
2. Adjust parameters for your setup
3. Mount servo to move something fun

### Intermediate
4. Add second servo for 2D tracking (pan + tilt)
5. Create pan-tilt camera mount
6. Add gesture recognition

### Advanced
7. Build robot arm with multiple servos
8. Add autonomous modes
9. Create mobile robot with hand steering

---

## 💡 Ideas for Extension

- **Pan-Tilt Camera**: Mount camera on servos, follows your hand
- **Robot Head**: Servo turns robot head to look at you
- **Laser Pointer**: Mount laser, point at things with your hand
- **Mobile Robot**: Hand controls robot driving direction
- **Robot Arm**: Multiple servos controlled by hand position
- **Interactive Art**: Create moving sculptures
- **Security Camera**: Auto-tracking security camera
- **Pet Toy**: Move toy for your pet to chase

---

## 🆘 Need Help?

1. **Check guides**: Read `HAND_ROBOT_GUIDE.md` for detailed help
2. **Test hardware**: Run `python test_hardware.py`
3. **Check basics**: Ensure `source ../setup_env.sh` was run
4. **Simple test**: Try the basic examples first (detection.py, pose_estimation.py)

---

## 📋 Hardware Checklist

Before running, ensure you have:
- [ ] Raspberry Pi 5 with Hailo-8L
- [ ] Servo motor (SG90 or similar)
- [ ] LED (any color)
- [ ] 220Ω resistor
- [ ] Camera (USB or RPi CSI)
- [ ] Jumper wires
- [ ] Power supply (5V 5A)
- [ ] Servo connected to GPIO 17
- [ ] LED + resistor connected to GPIO 20
- [ ] Camera connected and working

---

**Created**: 2025-01-16
**Platform**: Raspberry Pi 5 + Hailo-8L
**Model**: YOLOv8s Pose Estimation

🎉 **Have fun controlling your robot with your hands!** 🤖✋

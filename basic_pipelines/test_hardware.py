#!/usr/bin/env python3
"""
Hardware Test Script for Hand-Controlled Robot

Tests servo and LED connections before running the main application.
Run this first to verify your hardware setup is correct.

Usage:
    python test_hardware.py
    python test_hardware.py --servo-pin 17 --led-pin 20
"""

import argparse
import time
import sys

def test_led(pin):
    """Test LED connection."""
    print(f"\n{'='*60}")
    print(f"  Testing LED on GPIO {pin}")
    print(f"{'='*60}")

    try:
        from gpiozero import LED

        led = LED(pin)
        print("✓ LED object created successfully")

        print("\nBlinking LED 5 times...")
        for i in range(5):
            led.on()
            print(f"  {i+1}. LED ON  - Do you see the LED lit?")
            time.sleep(0.5)

            led.off()
            print(f"     LED OFF")
            time.sleep(0.5)

        led.close()
        print("\n✓ LED test completed successfully!")
        return True

    except Exception as e:
        print(f"\n✗ LED test FAILED: {e}")
        print("\nTroubleshooting:")
        print("  1. Check LED is connected to the correct GPIO pin")
        print("  2. Check LED polarity (long leg = +, short leg = -)")
        print("  3. Ensure 220Ω resistor is in series")
        print(f"  4. Try a different pin with: python test_hardware.py --led-pin 21")
        return False


def test_servo(pin, use_pigpio=False):
    """Test servo connection."""
    print(f"\n{'='*60}")
    print(f"  Testing Servo on GPIO {pin}")
    print(f"{'='*60}")

    try:
        from gpiozero import Servo
        from gpiozero.pins.pigpio import PiGPIOFactory

        # Create servo with optional pigpio
        if use_pigpio:
            print("Using pigpio for better PWM control...")
            try:
                factory = PiGPIOFactory()
                servo = Servo(pin, pin_factory=factory)
                print("✓ Servo created with pigpio")
            except Exception as e:
                print(f"⚠ Could not use pigpio: {e}")
                print("  Falling back to default pins...")
                servo = Servo(pin)
        else:
            servo = Servo(pin)
            print("✓ Servo object created successfully")

        print("\nTesting servo movements...")
        print("Watch the servo - it should move through its range.")
        print()

        # Test sequence
        tests = [
            ("Center (0°)", 0.0, "Servo should be at center position"),
            ("Right (-90°)", -1.0, "Servo should move to minimum (right/clockwise)"),
            ("Center (0°)", 0.0, "Servo should return to center"),
            ("Left (+90°)", 1.0, "Servo should move to maximum (left/counter-clockwise)"),
            ("Center (0°)", 0.0, "Servo should return to center"),
        ]

        for i, (name, position, description) in enumerate(tests, 1):
            print(f"{i}. {name}")
            print(f"   Setting servo.value = {position}")
            servo.value = position
            print(f"   → {description}")
            time.sleep(1.5)

        # Sweep test
        print("\n6. Smooth sweep test")
        print("   Servo should smoothly sweep left to right")
        for pos in range(-10, 11):
            servo.value = pos / 10.0
            time.sleep(0.05)

        print("   Servo should smoothly sweep right to left")
        for pos in range(10, -11, -1):
            servo.value = pos / 10.0
            time.sleep(0.05)

        # Return to center
        servo.mid()
        print("\n7. Returning to center position")
        time.sleep(1)

        servo.close()
        print("\n✓ Servo test completed successfully!")
        return True

    except Exception as e:
        print(f"\n✗ Servo test FAILED: {e}")
        print("\nTroubleshooting:")
        print("  1. Check servo connections:")
        print("     - Brown/Black wire → GND")
        print("     - Red wire → 5V")
        print(f"     - Orange/Yellow wire → GPIO {pin}")
        print("  2. Ensure servo has adequate power supply")
        print("  3. Try using pigpio for better control:")
        print("     sudo apt-get install pigpio")
        print("     sudo systemctl start pigpiod")
        print("     python test_hardware.py --use-pigpio")
        print(f"  4. Try a different pin: python test_hardware.py --servo-pin 18")
        return False


def test_camera(device):
    """Test camera connection."""
    print(f"\n{'='*60}")
    print(f"  Testing Camera: {device}")
    print(f"{'='*60}")

    try:
        import cv2

        print(f"Opening camera device: {device}")

        if device == "rpi":
            print("⚠ RPi camera testing requires GStreamer")
            print("  Run this command to test RPi camera:")
            print("  gst-launch-1.0 libcamerasrc ! videoconvert ! autovideosink")
            return None

        # Try to open camera
        cap = cv2.VideoCapture(device if device.startswith('/dev/') else int(device))

        if not cap.isOpened():
            print(f"✗ Could not open camera: {device}")
            return False

        print("✓ Camera opened successfully")

        # Read a frame
        ret, frame = cap.read()
        if not ret:
            print("✗ Could not read frame from camera")
            cap.release()
            return False

        height, width = frame.shape[:2]
        print(f"✓ Camera resolution: {width}x{height}")
        print(f"✓ Successfully captured test frame")

        cap.release()
        print("\n✓ Camera test completed successfully!")
        return True

    except ImportError:
        print("⚠ OpenCV not installed. Skipping camera test.")
        print("  Install with: pip install opencv-python")
        return None
    except Exception as e:
        print(f"\n✗ Camera test FAILED: {e}")
        print("\nTroubleshooting:")
        print("  1. Check camera is connected")
        print("  2. List available cameras: ls /dev/video*")
        print("  3. For RPi camera, ensure it's enabled in raspi-config")
        return False


def test_hailo():
    """Test Hailo module import."""
    print(f"\n{'='*60}")
    print(f"  Testing Hailo Module")
    print(f"{'='*60}")

    try:
        import hailo
        print("✓ Hailo module imported successfully")

        # Check environment
        import os
        tappas_dir = os.environ.get('TAPPAS_POST_PROC_DIR', '')
        if tappas_dir:
            print(f"✓ TAPPAS_POST_PROC_DIR set to: {tappas_dir}")
        else:
            print("⚠ TAPPAS_POST_PROC_DIR not set")
            print("  Run: source ../setup_env.sh")

        print("\n✓ Hailo test completed successfully!")
        return True

    except ImportError as e:
        print(f"✗ Hailo module import FAILED: {e}")
        print("\nTroubleshooting:")
        print("  1. Ensure you're in the Hailo virtual environment")
        print("  2. Run: source ../setup_env.sh")
        print("  3. Check Hailo installation")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test hardware for hand-controlled robot")
    parser.add_argument("--servo-pin", type=int, default=17, help="GPIO pin for servo (default: 17)")
    parser.add_argument("--led-pin", type=int, default=20, help="GPIO pin for LED (default: 20)")
    parser.add_argument("--camera", default="/dev/video0", help="Camera device (default: /dev/video0)")
    parser.add_argument("--use-pigpio", action="store_true", help="Use pigpio for servo control")
    parser.add_argument("--skip-camera", action="store_true", help="Skip camera test")
    parser.add_argument("--skip-servo", action="store_true", help="Skip servo test")
    parser.add_argument("--skip-led", action="store_true", help="Skip LED test")
    parser.add_argument("--skip-hailo", action="store_true", help="Skip Hailo test")

    args = parser.parse_args()

    print("="*60)
    print("  HAND-CONTROLLED ROBOT - Hardware Test")
    print("="*60)
    print("\nThis script will test your hardware connections.")
    print("Press Ctrl+C at any time to exit.\n")

    results = {}

    # Test Hailo first
    if not args.skip_hailo:
        results['hailo'] = test_hailo()
        time.sleep(1)

    # Test LED
    if not args.skip_led:
        input("\nPress Enter to test LED...")
        results['led'] = test_led(args.led_pin)
        time.sleep(1)

    # Test Servo
    if not args.skip_servo:
        input("\nPress Enter to test Servo (ensure servo has room to move)...")
        results['servo'] = test_servo(args.servo_pin, args.use_pigpio)
        time.sleep(1)

    # Test Camera
    if not args.skip_camera:
        input("\nPress Enter to test Camera...")
        results['camera'] = test_camera(args.camera)
        time.sleep(1)

    # Summary
    print("\n" + "="*60)
    print("  TEST SUMMARY")
    print("="*60)

    for component, result in results.items():
        if result is True:
            status = "✓ PASSED"
            symbol = "✓"
        elif result is False:
            status = "✗ FAILED"
            symbol = "✗"
        else:
            status = "⊘ SKIPPED"
            symbol = "⊘"

        print(f"{symbol} {component.upper()}: {status}")

    # Overall status
    print()
    if all(r in [True, None] for r in results.values()):
        print("🎉 All tests passed! Your hardware is ready.")
        print("\nNext step:")
        print("  python hand_controlled_servo.py -i /dev/video0 --use-frame --show-fps")
    elif any(r is False for r in results.values()):
        print("⚠ Some tests failed. Please check the troubleshooting steps above.")
        print("\nYou can still try running the main program, but hardware may not work correctly.")
    else:
        print("ℹ Tests completed with warnings. Review output above.")

    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

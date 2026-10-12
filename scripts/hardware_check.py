"""Bench checks for the relay board and MQTT devices.

    python3 scripts/hardware_check.py              what the Pi, the pins and the house look like now
    python3 scripts/hardware_check.py click 3      switch relay channel 3 on for a second, then off
    python3 scripts/hardware_check.py click 3 --active-high     the same, for a board that is the other way round

The report only reads. `click` drives one pin and releases it; it refuses a
channel the running service already holds (use the Devices page for those).
Like seed_drishti_devices.py this imports drishti_config, never Garuda_web, so
it starts no camera and takes no pin from the service.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from basic_pipelines.drishti_config import CHANNEL_TO_PIN, DATA_DIR  # noqa: E402

SERVICE = "http://127.0.0.1:8080"
OK, WARN, BAD = "ok  ", "WARN", "FAIL"


def _run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _load(name):
    try:
        return json.loads((Path(DATA_DIR) / name).read_text())
    except Exception:
        return []


def _setting(name, default=""):
    """A setting as the service will see it: its environment, else .env."""
    if name in os.environ:
        return os.environ[name]
    try:
        for line in (ROOT / ".env").read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip("'\"")
    except OSError:
        pass
    return default


def active_high():
    return _setting("RELAY_ACTIVE_HIGH").lower() in ("1", "true", "yes", "on")


def pin_state(pin):
    """('output' | 'input' | '?', 'hi' | 'lo' | '--') from pinctrl."""
    line = _run("pinctrl", "get", str(pin))
    found = re.search(r":\s*(\w+).*\|\s*(\S+)", line)
    if not found:
        return "?", "--"
    mode = {"op": "output", "ip": "input", "no": "input"}.get(found.group(1), found.group(1))
    return mode, found.group(2)


def _port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _get(path):
    try:
        with urllib.request.urlopen(SERVICE + path, timeout=4) as reply:
            return json.loads(reply.read())
    except Exception:
        return None


def report():
    worst = OK

    def say(level, text):
        nonlocal worst
        if level == BAD or (level == WARN and worst == OK):
            worst = level
        print(f"[{level}] {text}")

    print("Pi")
    throttled = _run("vcgencmd", "get_throttled").partition("=")[2]
    say(OK if throttled == "0x0" else WARN,
        f"power/throttle flags {throttled or 'unknown'} (0x0 is clean; anything else is under-voltage or heat)")
    say(OK, _run("vcgencmd", "measure_temp") or "temperature unknown")
    try:
        import gpiozero  # noqa: F401
        import lgpio  # noqa: F401
        say(OK, "gpiozero and lgpio import")
    except Exception as exc:
        say(BAD, f"GPIO libraries: {exc} (run after `source setup_env.sh`)")

    print("\nRelay board")
    high = active_high()
    on_level = "hi" if high else "lo"
    say(OK, f"polarity: active-{'high' if high else 'low'} "
            f"(a relay is ON when its pin is {on_level}; RELAY_ACTIVE_HIGH in .env changes it)")
    devices = _load("devices.json")
    by_channel = {d["transport"].get("channel"): d for d in devices
                  if d.get("transport", {}).get("kind") == "relay"}
    for channel, pin in CHANNEL_TO_PIN.items():
        mode, level = pin_state(pin)
        device = by_channel.get(channel)
        name = f"{device['id']} ({device['type']})" if device else "free"
        if mode == "output":
            now = "relay ON" if level == on_level else "relay off"
        else:
            now = "not driven"
        print(f"       IN{channel}  GPIO{pin:<2}  {mode:<6} {level:<2}  {now:<10}  {name}")
    energised = [c for c, p in CHANNEL_TO_PIN.items() if pin_state(p) == ("output", on_level)]
    if energised:
        say(WARN, f"channels {energised} are being driven ON right now: "
                  "a load wired to them switches on the moment the board is connected")

    print("\nHouse")
    relay_ids = {d["id"] for d in by_channel.values()}
    holding = []
    for rule in _load("rules.json"):
        if rule.get("enabled", True):
            for step in rule.get("then", []):
                if step.get("device") in relay_ids and step.get("action") == "on":
                    holding.append(f"rule {rule.get('id')}: {rule.get('source_utterance', '')[:60]!r}")
                    break
    for item in _load("schedules.json"):
        target = item.get("target", {})
        if item.get("enabled") and target.get("device") in relay_ids and target.get("action") == "on":
            holding.append(f"schedule {item.get('id')} at {item.get('time')}")
    say(OK, f"{len(devices)} devices, {len(relay_ids)} on relays")
    for line in holding:
        say(WARN, f"switches a relay on by itself: {line}")

    print("\nMQTT devices")
    host = _setting("DRISHTI_MQTT_HOST", "localhost")
    say(OK if _port_open(host, 1883) else BAD, f"broker {host}:1883")
    mqtt = [d for d in devices if d.get("transport", {}).get("kind") == "mqtt"]
    for d in mqtt:
        base = d["transport"].get("topic_base")
        print(f"       {d['id']}: listens on {base}/set, reports on {base}/state")
    if not mqtt:
        print("       none registered (a device listens on <topic>/set and reports on <topic>/state)")

    print("\nService")
    health, ready = _get("/api/health"), _get("/api/ready")
    if health is None:
        say(BAD, "garuda-web does not answer on 127.0.0.1:8080")
    else:
        say(OK, f"garuda-web up, commit {health.get('commit')}")
        down = [name for name, good in (ready or {}).get("checks", {}).items() if not good]
        say(BAD if down else OK, f"not ready: {down}" if down else "camera, rule loop, workers, disk ready")

    print(f"\nResult: {'ready' if worst == OK else 'look at the ' + worst + ' lines above'}")
    return 0 if worst != BAD else 1


def click(channel, seconds, high):
    if channel not in CHANNEL_TO_PIN:
        raise SystemExit(f"channel must be one of {sorted(CHANNEL_TO_PIN)}")
    pin = CHANNEL_TO_PIN[channel]
    from gpiozero import OutputDevice
    try:
        relay = OutputDevice(pin, active_high=high, initial_value=False)
    except Exception as exc:
        raise SystemExit(f"GPIO{pin} is in use ({exc}). If the service holds this channel, "
                         "switch it from the Devices page instead.")
    try:
        print(f"IN{channel} (GPIO{pin}) on: the relay should click and its LED light")
        relay.on()
        time.sleep(seconds)
        relay.off()
        print("off: it should click back and the LED go dark")
        time.sleep(0.3)
    finally:
        relay.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    one = sub.add_parser("click", help="switch one relay channel on, then off")
    one.add_argument("channel", type=int)
    one.add_argument("--seconds", type=float, default=1.0)
    one.add_argument("--active-high", action="store_true", default=None,
                     help="try the opposite polarity without changing .env")
    args = parser.parse_args()
    if args.command == "click":
        high = active_high() if args.active_high is None else True
        return click(args.channel, args.seconds, high)
    return report()


if __name__ == "__main__":
    sys.exit(main())

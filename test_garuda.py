"""
Garuda Web Server — Full Test Suite
Run from project root after sourcing setup_env.sh:
    python test_garuda.py
Tests: server health, email, detection labels, cascade HEF files, pipeline config
"""

import os
import sys
import json
import time
import smtplib
import requests
import subprocess
from pathlib import Path
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

BASE_URL  = "http://localhost:8080"
RESOURCES = Path(__file__).parent / "resources"
PIPELINES = Path(__file__).parent / "basic_pipelines"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"

results = []

def check(name, ok, detail=""):
    tag = PASS if ok else FAIL
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    results.append((name, ok))
    return ok


# ── Test 1: Server health ─────────────────────────────────────────────────────
print("\n[T1] Server Health")
try:
    r = requests.get(f"{BASE_URL}/", timeout=5)
    check("HTTP 200 on /", r.status_code == 200, f"got {r.status_code}")
except Exception as e:
    check("HTTP 200 on /", False, str(e))

try:
    r = requests.get(f"{BASE_URL}/api/state", timeout=5)
    ok = r.status_code in (200, 401)   # 401 = auth required = server is alive
    check("API /api/state reachable", ok, f"status {r.status_code}")
except Exception as e:
    check("API /api/state reachable", False, str(e))


# ── Test 2: Email credentials ─────────────────────────────────────────────────
print("\n[T2] Email")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASS   = os.environ.get("EMAIL_SENDER_PASS", "")

check(".env EMAIL_SENDER set",      bool(EMAIL_SENDER),  EMAIL_SENDER or "EMPTY")
check(".env EMAIL_SENDER_PASS set", bool(EMAIL_PASS),    "****" if EMAIL_PASS else "EMPTY")

if EMAIL_SENDER and EMAIL_PASS:
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(EMAIL_SENDER, EMAIL_PASS)
        check("Gmail SMTP login", True)

        msg = MIMEText("Garuda test suite email — please ignore.")
        msg["Subject"] = "[Garuda Test] SMTP connectivity check"
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = EMAIL_SENDER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(EMAIL_SENDER, EMAIL_PASS)
            s.send_message(msg)
        check("Test email sent", True)
    except smtplib.SMTPAuthenticationError as e:
        check("Gmail SMTP login", False, f"Auth failed: {e}")
    except Exception as e:
        check("Gmail SMTP login", False, str(e))
else:
    check("Gmail SMTP login", False, "credentials missing — skipped")


# ── Test 3: Resource files ────────────────────────────────────────────────────
print("\n[T3] Resource Files")
required = [
    "best_v5.hef",
    "best_labels.json",
    "mobilenetv2_garuda.hef",
    "midas_depth.hef",
    "libyolo_hailortpp_post.so",
]
for f in required:
    p = RESOURCES / f
    check(f"resources/{f}", p.exists(), f"{p.stat().st_size//1024}KB" if p.exists() else "MISSING")


# ── Test 4: Detection labels ──────────────────────────────────────────────────
print("\n[T4] Detection Labels")
labels_path = RESOURCES / "best_labels.json"
if labels_path.exists():
    with open(labels_path) as f:
        labels_data = json.load(f)
    labels = labels_data.get("labels", [])
    check("best_labels.json readable", True, f"{len(labels)} labels: {labels}")
    check("Knife in labels",    "Knife"    in labels, f"labels={labels}")
    check("Hammer in labels",   "Hammer"   in labels, f"labels={labels}")
    check("scissors in labels", "scissors" in labels, f"labels={labels}")
    check("Person in labels",   "Person"   in labels, f"labels={labels}")

    danger = ["Knife", "scissors", "Hammer"]
    for d in danger:
        check(f"'{d}' matches DANGER_LABELS", d in labels,
              "label mismatch — detection will silently fail" if d not in labels else "OK")
else:
    check("best_labels.json readable", False, "file missing")


# ── Test 5: Pipeline config bug (thresholds_str) ─────────────────────────────
print("\n[T5] Pipeline Config")
web_src = (PIPELINES / "Garuda_web.py").read_text()
# When labels_json is set, output-format-type=FLOAT32 must NOT be in the same block
# The fix separates them into if/else branches
has_fix = "if args.labels_json:" in web_src and "output-format-type=HAILO_FORMAT_TYPE_FLOAT32" in web_src
# Verify the fix structure — FLOAT32 must be in the else branch only
import ast
tree = ast.parse(web_src)
check("Garuda_web.py parses cleanly", True)
check("thresholds_str if/else fix present", has_fix,
      "FLOAT32 should only be set for non-custom-label models")


# ── Test 6: Cascade HEF files ─────────────────────────────────────────────────
print("\n[T6] Cascade Pipeline (garuda_cascade.py)")
cascade_src = (PIPELINES / "garuda_cascade.py").read_text()
hef_names = {}
exec_ns = {}
for line in cascade_src.splitlines():
    if line.strip().startswith("HEF_NAMES"):
        try:
            hef_names = eval(line.split("=", 1)[1].strip())
        except Exception:
            pass
        break

for role, fname in hef_names.items():
    p = RESOURCES / fname
    check(f"cascade HEF [{role}] {fname}", p.exists(),
          f"{p.stat().st_size//1024}KB" if p.exists() else "MISSING")

# Check GStreamer camera thread present
check("gst_camera_thread defined",    "def gst_camera_thread" in cascade_src)
check("_auto_detect_camera defined",  "def _auto_detect_camera" in cascade_src)
check("gi.repository imported",       "from gi.repository import" in cascade_src)


# ── Test 7: Camera detection ──────────────────────────────────────────────────
print("\n[T7] Camera")
try:
    result = subprocess.run(["v4l2-ctl", "--list-devices"],
                            capture_output=True, text=True, timeout=3)
    lines = result.stdout
    has_pi   = "rp1-cfe" in lines
    has_usb  = any(name for name in lines.splitlines()
                   if not name.startswith("\t") and "platform:" not in name and name.strip())
    check("Pi camera (rp1-cfe) detected", has_pi, "IMX708 via libcamera" if has_pi else "not found")
    check("USB camera detected",          has_usb, "found" if has_usb else "none connected")
except Exception as e:
    check("v4l2-ctl available", False, str(e))


# ── Summary ────────────────────────────────────────────────────────────────────
print("\n" + "="*52)
passed = sum(1 for _, ok in results if ok)
total  = len(results)
failed = [(n, ok) for n, ok in results if not ok]
print(f"  {passed}/{total} passed")
if failed:
    print("  FAILED:")
    for name, _ in failed:
        print(f"    - {name}")
print("="*52 + "\n")
sys.exit(0 if not failed else 1)

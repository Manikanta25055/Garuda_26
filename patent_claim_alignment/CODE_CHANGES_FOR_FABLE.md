# Code Changes for Fable — bring `Garuda_web.py` / `garuda_cascade.py` into agreement with the AR933 claims

**Scope:** parameter- and behaviour-level fixes only. These keep the drafted claims true.
Do **not** attempt the architectural rework (live production secondary, weapon→secondary
routing, sub-4 ms latency) — those are being handled by claim amendments, not code.

**Before you start:** branch `claim-alignment-bucket1` already has partial work. For each
item below, **check the current state first** — some are likely already done. Locate code by
string/symbol, not by the audit-time line numbers (they have shifted). After changes, run the
test suite under `tests/`.

Legend: **[verify]** = probably already applied on this branch, confirm; **[todo]** = expected
to still need doing.

---

## 1. Three-consecutive-frame alert gate — Claim 3  **[verify]**
- **Claim:** alert fires only after the detection is confirmed over **3 consecutive frames**.
- **Fix:** the danger-alert gate must require `>= 3` consecutive confirming frames (was `>= 2`).
- **Where:** `Garuda_web.py`, the danger-detection / alert-decision path (search for the
  consecutive-frame counter; a comment `require 3 consecutive frames to fire (Claim 3)` is
  already present near the gate).

## 2. Asymmetric confidence thresholds in production — Claim 4  **[verify]**
- **Claim:** person class threshold **0.60**; weapon classes threshold **0.25–0.35**.
- **Fix:** the production server must apply per-class thresholds, not one global
  `DETECTION_THRESHOLD`. Constants `PERSON_CONF_THRESHOLD = 0.60` and
  `WEAPON_CONF_THRESHOLD = 0.25` appear to exist — confirm they are actually **applied** at
  the detection-filtering site for the right classes (Person vs Hammer/Knife/Scissors), not
  just declared.
- **Where:** `Garuda_web.py` (threshold constants near the top; application in the
  detection-processing callback).

## 3. Per-label alert cooldown — cooldown embodiment  **[verify]**
- **Claim/spec:** a **per-detection-class** 60 s cooldown (each class has its own cooldown).
- **Fix:** replace the single global `__danger__` cooldown key with a **per-label** key so
  each class (Hammer / Knife / Person / Scissors) cools down independently.
- **Where:** `Garuda_web.py`, the email/alert cooldown logic (search `EMAIL_COOLDOWN` and the
  cooldown dictionary/key).

## 4. OTP expiry window = 10 minutes  **[verify]**
- **Spec:** OTP second factor valid for **10 minutes**.
- **Fix:** OTP expiry must be **600 s** (was 300 s / 5 min). Change the OTP expiry constant
  and any comparison that uses it. (Note: `_LOGIN_LOCKOUT_SECONDS = 300` is a *different*
  value — the login lockout, not the OTP — leave it alone.)
- **Where:** `Garuda_web.py`, OTP generation/verification (search `otp` / expiry).

## 5. Local (offline) speech-to-text — privacy claim  **[todo]**
- **Spec:** the voice assistant performs STT **locally** and transmits **no audio** to any
  external service.
- **Problem:** current code calls `recognizer.recognize_google(audio)`, which **uploads raw
  audio to Google**, contradicting the privacy claim.
- **Fix:** swap to an **offline** recognizer (e.g. **Vosk** or **whisper.cpp**) so audio never
  leaves the device. Keep the existing behaviour of sending only **unmatched-command text**
  (not audio) to the external LLM.
- **Where:** `Garuda_web.py` (search `recognize_google`).

## 6. Watchdog — 10 s inference heartbeat + auto-restart  **[todo]**
- **Spec:** watchdog polls an **inference heartbeat every 10 s** and **auto-restarts the
  multimedia pipeline** after 180 s of silence.
- **Current:** polls an **HTTP** heartbeat every **60 s**; on 180 s absence sends a tamper
  email (restart is left to a systemd service).
- **Fix:** (a) change poll interval to **10 s**; (b) add a real **inference heartbeat** emitted
  by the primary pipeline (not just the HTTP endpoint); (c) keep relying on
  `garuda-monitor.service` for the actual process restart.
- **Where:** `Garuda_web.py`, watchdog/heartbeat section (search `heartbeat`, `180`).

## 7. Core-affinity mapping — Claim 1 core assignment  **[todo]**  *(garuda_cascade.py)*
- **Spec:** Core 0 = camera, Core 1 = NPU-retrieval, **Core 2 = secondary-verification NN**,
  Core 3 = auxiliary services.
- **Current:** Core 1 runs both primary and secondary; Core 2 does NumPy/logging only;
  Core 3 unused.
- **Fix:** re-pin secondary-NN inference to **Core 2** and auxiliary services to **Core 3** to
  match the spec mapping. (Lower risk than it looks — it is `os.sched_setaffinity` placement.)
- **Where:** `garuda_cascade.py` (search `sched_setaffinity` / affinity).

## 8. (OPTIONAL) Blur all streamed frames in every mode — egress privacy  **[optional]**
- **Only do this if the patent agent decides to keep the *absolute* "no raw frames leave the
  device" wording** in Claims 1/7. Our current recommendation is to instead **narrow the
  claim** to the third-party boundary (see the PDF, Clause 1B), in which case **no code change
  is needed** and you should skip this item.
- **If required:** force the privacy Gaussian blur (or a box mask) on **all** MJPEG/WebRTC
  streamed frames in **every** operating mode, so unobscured video never egresses.
- **Where:** `Garuda_web.py` (streaming/MJPEG path; the privacy-blur function already exists —
  apply it unconditionally to the stream).

---

## Explicitly out of scope (do NOT implement)
- Making the **production secondary network live** (it is currently a stub that logs and
  returns) — handled by amending Claims 1/5/7.
- Routing **weapon detections through the secondary** — handled by claim amendment.
- Hitting a **sub-4 ms secondary latency** — physically impossible for MobileNetV2 (~36 ms) +
  MiDaS (~29 ms) on this hardware; handled by amending Claim 5.
- Changing **AES-GCM to CBC** — GCM is stronger; the spec wording is being amended to GCM
  instead. Leave the crypto code as is.

## When done
- Run `tests/` and confirm green.
- Summarise which of items 1–7 were already applied vs newly changed, so we can tell the
  patent agent the "keep as drafted" claims are now truthfully enabled.

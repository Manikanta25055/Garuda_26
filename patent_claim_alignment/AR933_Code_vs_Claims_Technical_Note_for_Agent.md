# AR933 — Technical Reconciliation Note: Codebase vs. Complete Specification

**To:** Adv. Pranav Bhat (IN/PA 4580), Patent Agent
**From:** Gonugondla Veera Manikanta (Inventor)
**Re:** "A System and Method for Privacy-Preserving Edge-Based Threat Detection in a Domestic Environment"
**Application:** AR933 (Complete Specification, Form 2) — Claims 1–11
**Purpose:** Reconcile the drafted claims/description with the actual working code before prosecution. This note lists (A) what the claims assert that the implementation **cannot** deliver as worded and why, (B) exactly what the code **does implement today**, and (C) the **alternative language / features we can support**, so the claims can be amended to match a system that is real and enabled.

**Basis of this note:** Source audit of repository `Manikanta25055/Garuda_26` (default branch `main`), files `basic_pipelines/garuda_cascade.py`, `basic_pipelines/Garuda_web.py`, `basic_pipelines/cascade_config.json`, `resources/best_labels.json`. File and line references are given throughout so the analysis can be verified independently.

---

## 0. The one structural fact everything else follows from

The specification describes **one** system: a capacity-2 bounded queue that feeds cropped image regions to a **live secondary verification neural network**, whose verification output **gates the alert**, with **weapon detections routed through that verifier**.

In the actual code this is split across two programs, and the piece that joins them is not live:

- **`Garuda_web.py`** — the production web/alert server. It contains the bounded queue exactly as claimed (`SECONDARY_QUEUE_SIZE = 2`, non-blocking `put_nowait`, intentional drop; lines 399–405, 1441–1444). **However, the secondary worker that consumes the queue is a stub** — it logs the event and returns without running any neural network (lines 445–478: *"Secondary inference placeholder … For now: log the event, record completion metric."*). The alert is therefore generated from the **primary** YOLO detector alone (lines 1372–1418).
- **`garuda_cascade.py`** — a standalone program that **does** run all three real models (YOLO + MobileNetV2 + MiDaS), **but** it has **no capacity-2 bounded queue** (its queues are `maxsize=4` and `maxsize=16`; lines 666–667), the secondary runs **synchronously in-line** on the same thread and same chip (so it is *not* decoupled from the primary), and **weapon detections bypass the secondary entirely** (lines 439–447).

So the claimed architecture — bounded-2 queue + **live** secondary whose verdict gates the alert + weapons verified by the secondary — exists as a working whole in **neither** file. Every item in Section A flows from this.

---

## A. What the claims assert that we CANNOT deliver as worded (with reasons)

### A1. Claim 5 — secondary verification "within a combined latency of less than 4 milliseconds per cropped image region"
**Reason it cannot be met:** The two real secondary models are MobileNetV2 (Safe/Weapon classifier) and MiDaS Small (depth). The code's own measured figures are **MobileNet ≈ 36 ms** and **MiDaS ≈ 29 ms** per crop on the Hailo-8L (`garuda_cascade.py` lines 451, 457). No software change reduces a real MobileNet+MiDaS forward pass on this accelerator to 4 ms; it is a hardware/model-size limit. The detailed description's derived figure of a "3.6–8.6 ms remaining window" for the secondary stage (spec ¶ referencing the 18.4 ms primary budget) is therefore not supported by the hardware.
**This limitation must be amended, not coded around.**

### A2. "Every weapon-class detection is independently re-evaluated by the secondary before an alert is generated" (Object of the Invention; Claims 1 & 7)
**Reason it cannot be met as worded — two independent reasons:**
1. **Direction is inverted in the design.** In the code the weapon classes (Hammer/Knife/Scissors) are treated as **definitive outputs of the primary** and are **not** sent to the secondary at all (`garuda_cascade.py` 439–447; in `Garuda_web.py` the danger labels trigger the alert directly, 1372–1418). The secondary (MobileNetV2 + MiDaS) is applied only to **person** crops. So the invention as built does *not* re-verify weapons with a second network.
2. **The bounded queue is a *dropping* queue.** By design, when the secondary falls behind, incoming crops are **discarded** to protect primary throughput (`Garuda_web.py` 401, 1441–1444; `garuda_cascade.py` 344–347). "Every detection is re-evaluated" and "the primary is never delayed and the queue drops crops under load" are **mutually exclusive**. Under load, verification is inherently **best-effort**, not exhaustive.

### A3. The function the secondary actually performs is not "confirming or rejecting the classification assigned by the primary" for a weapon
**Reason:** The MobileNetV2 model is a binary **Safe vs. Weapon** classifier run on a **person** crop (`garuda_cascade.py` 82, 302–312, 449–454) — i.e., "is this person holding/associated with a weapon," combined with a MiDaS **depth-variance anti-spoof** that rejects flat-screen/photo presentations (lines 220–227, 348–349 of the spec). It is **not** a network that re-verifies whether a Hammer/Knife/Scissors class label from the primary is correct. Claim 1's phrase "re-evaluate a cropped image region … confirming or rejecting a classification assigned by the primary detection neural network" describes a different function from what the model does.

### A4. Consequence for the empirical record (TABLE.1 and FIGs. 3–4) — please verify before filing/prosecuting
Because the **production** secondary is a stub (Section 0), the headline measurements — **52.26 ± 0.76 FPS "under full concurrent load," the "< 4 ms" secondary, and the 22–27 ms end-to-end cascade latency** — appear to have been recorded on a system in which **the second neural network was not actually executing**. If that is how they were measured, the data does **not** substantiate the claimed cascade and cannot be repaired by drafting; it would require **re-measurement with a live secondary in the loop**. Please confirm the measurement conditions for every TABLE.1 row and FIG. 3–4 figure.

> **Net effect of Section A:** Claim 5 (as a number) and the "every weapon re-evaluated by the secondary" framing (Claims 1/7 and the Objects) cannot be truthfully claimed against the present system. They must be **amended** using the alternatives in Section C, and the empirical basis (A4) must be confirmed.

---

## B. What the code ACTUALLY implements today (complete inventory, so nothing is hidden)

### B.1 Correctly implemented — matches the specification
| # | Claimed feature | Status | Evidence (file : line) |
|---|---|---|---|
| 1 | Four detection classes: Hammer, Knife, Person, Scissors | Implemented | `resources/best_labels.json`; `garuda_cascade.py:79` |
| 2 | Three compiled INT8 HEF binaries on a **single HailoRT VDevice**, one network-group active at a time (sequential time-share) | Implemented | `garuda_cascade.py:98–102, 649–665`, activation `261–263, 308–309, 321–322` |
| 3 | Primary YOLOv8s detector; 640×640 input | Implemented | `garuda_cascade.py:85, 231–234`; HEFs `yolov8s_garuda.hef` / `best_v5.hef` |
| 4 | Depth-variance anti-spoof, threshold **0.005** (rejects flat-screen/photo person spoofs) | Implemented | `garuda_cascade.py:80, 220–227` |
| 5 | On-device Gaussian privacy blur on person bbox, **kernel 51, sigma 30**, in privacy mode | Implemented | `Garuda_web.py:1359–1371` |
| 6 | Password hashing **PBKDF2-SHA256, 600,000 iterations**, per-user salt | Implemented | `Garuda_web.py:260–266` |
| 7 | **Six-digit OTP** second factor, delivered by email | Implemented | `Garuda_web.py:938–946` |
| 8 | **HTTP-only** session cookies (samesite=lax) | Implemented | `Garuda_web.py:2461–2464, 2518, 2578–2580` |
| 9 | Encrypted evidence clip uploaded over **SSH/SFTP** to a **user-owned** remote store (`~/garuda_evidence/`), no third-party server | Implemented | `Garuda_web.py:133–150, 1202–1232` |
| 10 | AES-**256** encryption of the evidence clip at rest | Implemented (but **GCM**, not CBC — see C1) | `Garuda_web.py:1176–1190` |
| 11 | Five operating-mode flags (do-not-disturb, notification-disabled, idle, emergency, privacy) behind a **single lock** | Implemented | `Garuda_web.py:190–195, 209, 552, 1342` |
| 12 | Presence monitor: **ARP scan every 30 s**, **90 s grace window**, sets idle flag | Implemented | `Garuda_web.py:254, 1039–1051` |
| 13 | Persistent **SQLite** event store + JSON log + **catch-up** endpoint (`get_events_since`) | Implemented | `Garuda_web.py:82, 818–881` |
| 14 | Voice assistant ("Narada"): local wake word + command match; **unmatched** commands sent to external LLM (Groq `llama-3.3-70b`) as text | Implemented (but STT is cloud, not local — see C6) | `Garuda_web.py:129–130, 199, 1857–1878` |
| 15 | Capacity-**2** bounded queue with **intentional drop**, primary never blocks (structure only) | Implemented as structure; **consumer is a stub** (Section 0) | `Garuda_web.py:399–405, 445–478, 1441–1444` |
| 16 | Per-core CPU affinity via OS scheduler-affinity primitive | Implemented (mapping differs — see B.2) | `garuda_cascade.py:212–218, 330, 406, 482` |
| 17 | MJPEG + WebRTC live streaming to remote client | Implemented (raises a privacy-scope point — see C5) | `Garuda_web.py:90, 966–968` |

### B.2 Implemented, but with values/behaviour DIFFERENT from the claim (parameter-level; code-fixable — Section C)
| # | Claim/spec says | Code actually does | Evidence |
|---|---|---|---|
| 1 | Alert only after **3** consecutive confirming frames (Claim 3) | Fires after **2** consecutive frames | `Garuda_web.py:1374` (`>= 2`) |
| 2 | OTP expiry window **10 minutes** | **5 minutes** (300 s) | `Garuda_web.py:2561, 2626` |
| 3 | **Per-label** 60 s cooldown per detection class | **Global** 60 s cooldown (single `__danger__` key + global `EMAIL_COOLDOWN`) | `Garuda_web.py:124, 1414` |
| 4 | Asymmetric confidence thresholds person **0.60** / weapon **0.25–0.35** (Claim 4) | Only in standalone `garuda_cascade.py` (0.60 / 0.25). The **production** server uses one **global** threshold `DETECTION_THRESHOLD = 0.3` for all classes | `garuda_cascade.py:76–78` vs `Garuda_web.py:197, 1355` |
| 5 | Watchdog polls **inference heartbeat every 10 s**, **auto-restarts the multimedia pipeline** after 180 s | Polls an **HTTP** heartbeat **every 60 s**; on 180 s absence sends a **tamper email** (process restart is handled separately by the systemd service `garuda-monitor.service`) | `Garuda_web.py:213–214, 2166–2174` |
| 6 | Core-affinity: Core 0 camera, Core 1 NPU-retrieval, **Core 2 secondary-verification NN**, Core 3 auxiliary services | Core 0 camera, Core 1 runs **both** primary **and** secondary NN inference, Core 2 does NumPy/logging only, Core 3 unused; the web/aux server is a separate unpinned process | `garuda_cascade.py:20–24, 330, 406, 482` |

### B.3 Claimed but effectively absent in the running system
- **Live secondary verification in production** — stub (Section 0; `Garuda_web.py:445–478`).
- **Capacity-2 bounded crop queue in the model-running program** — absent from `garuda_cascade.py` (queues are 4 / 16; lines 666–667).
- **Weapon → secondary verification path** — absent; weapons bypass the secondary in both files (A2).

---

## C. What we CAN do instead — recommended amendments / alternatives

For each item: the honest options are (i) change the **code** to match the claim, or (ii) change the **claim** to match the code. Recommendation given per item.

### C1. AES mode (spec: CBC; code: GCM)
**Recommendation: amend the claim to "Galois/Counter Mode (GCM)."** The code uses AES-256-**GCM**, which is authenticated encryption and **stronger** than CBC. Rather than downgrade the code to CBC, change the specification wording from "cipher block chaining mode" / "AES-256-CBC" to "an authenticated 256-bit symmetric cipher operating in Galois/Counter Mode (AES-256-GCM)."

### C2. Secondary-stage latency (Claim 5, "< 4 ms")
**Recommendation: amend the number, or re-scope to the fast path.** Options:
- (a) Replace "< 4 ms" with the **actual measured** combined secondary latency (≈ 30–40 ms for MobileNet + MiDaS), and re-word the surrounding "real-time" statements accordingly; **or**
- (b) Claim the **lightweight RGB variance anti-spoof proxy** that genuinely runs in **< 1 ms** with no NPU (`garuda_cascade.py:88–90, 129–143`) as the fast daytime verification stage, and describe the MobileNet/MiDaS path as the higher-assurance mode. This keeps a truthful sub-millisecond figure.

### C3. "Every weapon re-evaluated by the secondary" + best-effort verification (A2)
**Recommendation: amend the claim to describe what the system truly does.** Two truthful re-framings, either or both:
- Re-scope the secondary's function to its real one: a **person-directed** stage — a MobileNetV2 Safe/Weapon classifier **plus** a MiDaS depth-variance **anti-spoof** that rejects flat-surface (photo/screen) person presentations — run on person crops. This is novel and defensible on its own.
- State that secondary verification is **best-effort under a bounded-drop policy**: the capacity-2 queue **intentionally discards** crops when the secondary is saturated, in order to guarantee the primary's frame rate. This is the actual inventive trade-off and should be claimed as such rather than "every detection."

**If instead you want to keep the original claim language, we would need to build it (see C7) — this is real engineering and would change the FPS/latency figures.**

### C4. Consecutive-frame gate (Claim 3: 3 frames; code: 2)
**Recommendation: fix in code** — change `>= 2` to `>= 3` (`Garuda_web.py:1374`). One line. Then the code matches Claim 3 and its 57.5 ms rationale. (Alternatively amend the claim to "at least two consecutive frames.")

### C5. Raw-video egress vs. live streaming (Claims 1/7: "no raw frames to any external device under any operating mode")
**Reason for conflict:** the system streams **MJPEG/WebRTC live video to remote browsers** (`Garuda_web.py:90, 966–968`); raw frames are only blurred in **privacy** mode. In normal mode, raw video does leave the host.
**Recommendation (pick one):**
- (a) **Narrow the claim** to the **inference/evidence data path** — i.e., "raw video frames are not transmitted to any **third-party** server; classification/inference outputs and unblurred evidence never leave the user-owned boundary" — which is true; **or**
- (b) **Fix in code:** force the privacy blur (or a box mask) on **all** streamed frames in every mode so no unobscured video ever egresses. We can do this.

### C6. Voice STT — spec says local, code sends audio to Google
**Reason:** the code calls `recognizer.recognize_google(audio)` (`Garuda_web.py:1878`), which **uploads the raw audio** to Google's cloud — directly contradicting the specification's statement that STT is performed locally and "at no point does the voice assistant transmit any audio recording … to any external service."
**Recommendation: fix in code** by swapping to an **offline** recognizer (e.g., Vosk or whisper.cpp) so audio never leaves the device; then the privacy claim is true. (Alternative: amend the spec to disclose that unmatched-command **audio** is sent to a third-party STT service — but this weakens the privacy story, so the code fix is preferred.)

### C7. Watchdog (Section B.2 #5)
**Recommendation: fix in code** — change the poll interval to **10 s**, add a true **inference heartbeat** from the primary pipeline (not just the HTTP endpoint), and rely on the existing `garuda-monitor.service` for the **auto-restart**. This makes the behaviour match the spec. (Alternative: amend the spec to describe the 60 s HTTP-heartbeat tamper-alert + systemd restart actually built.)

### C8. Per-class thresholds and per-label cooldown in production (Section B.2 #3, #4)
**Recommendation: fix in code** — add the asymmetric 0.60/0.25 thresholds and a per-label cooldown key to `Garuda_web.py` so the production server matches Claim 4 and the cooldown embodiment. Straightforward.

### C9. Core-affinity mapping (Section B.2 #6)
**Recommendation: fix in code or amend the description.** We can re-pin the secondary-NN inference to Core 2 and the auxiliary services to Core 3 to match the description; or amend the description to the actual mapping. Either is low-risk.

### C10. Bringing the two programs together (the real fix for Section 0 / A2, if you want to keep the original claims)
To make the **production** system match the original claims end-to-end we would:
1. Load the MobileNetV2 + MiDaS HEFs alongside the GStreamer pipeline. **Blocker flagged in the code itself** (`Garuda_web.py:451–456`): the GStreamer `hailonet` element owns the Hailo device, so the secondary needs either its own VDevice or the primary must be moved off GStreamer onto direct HailoRT. Non-trivial.
2. Route weapon (and/or person) crops through the **live** secondary and make the alert **wait on the verdict**.
3. **Re-measure** all TABLE.1 / FIG. 3–4 numbers with the secondary actually running (this is also what A4 requires).
**This is a multi-day engineering effort and will likely move the FPS/latency numbers.** It is the only path that preserves the claims as originally drafted.

---

## D. Recommended disposition at a glance

| Item | Cannot do as claimed | Fix in code | Amend claim/spec | Needs your decision |
|---|:--:|:--:|:--:|:--:|
| Claim 5 "< 4 ms" secondary | ✔ | | ✔ (C2) | |
| "Every weapon re-verified" / best-effort drop | ✔ | (C10, heavy) | ✔ (C3) | ✔ |
| Empirical data measured on stub (A4) | ✔ | (re-measure) | | ✔ (confirm) |
| AES CBC vs GCM | | (optional) | ✔ (C1) | |
| 3-frame gate | | ✔ (C4) | | |
| OTP 10 min | | ✔ | | |
| Per-label cooldown | | ✔ (C8) | | |
| Asymmetric thresholds in production | | ✔ (C8) | | |
| Raw-video egress vs streaming | | ✔ (C5b) | ✔ (C5a) | ✔ |
| Voice STT local vs Google cloud | | ✔ (C6) | (weak) | |
| Watchdog 10 s / inference heartbeat | | ✔ (C7) | (opt) | |
| Core-affinity mapping | | ✔ (C9) | (opt) | |

**Bottom line for the agent:** Items in the "Cannot do as claimed" column (Claim 5's 4 ms number, the "every weapon re-evaluated" framing, and the empirical basis in A4) **require claim amendment and/or re-measurement** — they cannot be closed by drafting alone. Everything else can be brought into agreement, most of it by small code changes we will make.

---

*Prepared from a line-by-line source audit. All file:line references are against `Manikanta25055/Garuda_26` branch `main`.*

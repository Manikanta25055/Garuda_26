# Patent Research — Project Garuda
**Invention:** A System and Method for Privacy-Preserving Real-Time Multi-Threat Detection Using a Cascaded Neural Processing Unit Pipeline on an Edge Computing Platform  
**Inventor:** Gonugondla Veera Manikanta  
**Status:** Provisional patent to be filed. 12-month window to add improvements before complete specification.  
**Date of research:** 2026-05-09

---

## Context: What Has Already Been Built

- **Hardware:** Raspberry Pi 5 + Hailo-8L NPU (13 TOPS INT8) + Sony IMX708 camera via CSI-2
- **Primary detector:** YOLOv8s INT8, 4 classes (Hammer, Knife, Person, Scissors), 52.2 FPS, 18.4 ms latency, mAP@0.5 = 0.854
- **Secondary verifier:** MobileNetV2 Safe/Weapon classifier, 99.0% accuracy (in-distribution)
- **Depth pre-filter:** MiDaS Small monocular depth variance gate (AUC 0.563 on CelebA-Spoof — near chance, admitted weakness)
- **Architecture:** Async CPU-pinned bounded-queue pipeline; Core 0 = camera, Core 1 = HailoRT, Core 2 = verifier, Core 3 = OS/web
- **Alert engine:** Mode-gated (DND / Privacy / Idle / Emergency / Active), AES-256-CBC encrypted clip upload via SSH, SMTPS with per-label 60 s cooldown
- **Privacy mode:** Gaussian blur on person bounding boxes before any output; no raw video leaves device
- **Voice assistant (Narada):** Wake-word gated, local rule dispatch, only text transcript sent externally for unmatched intents
- **Web:** FastAPI + Cloudflare Tunnel + PBKDF2-SHA256 (600k iterations) + WebRTC/MJPEG streaming
- **Power:** 5.89 W mean; BOM: USD 411 / INR 38,085
- **Dataset:** 6,226 images, 5,311 annotated instances, single-seed 80/10/10 stratified split

---

## 20 Novel Research Gaps (From Literature — New)

### A1 — Audio-Visual Fusion for Threat Verification
- **Problem:** System is purely visual. Audio events (glass breaking, gunshot, aggressive speech) carry orthogonal threat information that can reduce false positives and catch threats the camera misses.
- **Current best:** "Multimodal anomaly detection using video and audio fusion" (Scientific Reports, 2025); Seeed Sound Event Detection Module achieves ≥95% for glass break / gunshot on-device (2024); "Combining Audio and Video Analytics for Fight Detection" (Springer, 2025).
- **Why it matters for patent:** Adds a second independent modality — a novel claim layer on top of all existing vision-only claims. Directly addresses Gap 1 (continuous deployment metrics) and Gap 12 (open-set evaluation).
- **Feasibility on Pi 5 + Hailo-8L (12 months):** High — USB mic + lightweight audio CNN (YAMNet/LEAF) runs entirely on Core 3; no Hailo resources needed.
- **Implementation hint:** Run YAMNet INT8 on Core 3. Use audio threat score as a confidence multiplier before triggering alert. Late fusion (score product) first, cross-attention if time permits.

---

### A2 — Temporal Threat Trajectory Prediction (Intent Estimation)
- **Problem:** System detects per-frame weapon presence. Cannot distinguish "knife on counter" from "knife raised and advancing toward person." Intent requires reasoning over a sequence of positions and poses.
- **Current best:** Spatio-temporal action detection with tracking-based two-stage frameworks (Electronics/MDPI, 2024); "End-to-End Temporal Action Detection with 1B Parameters" (CVPR 2024) — too large for edge; lightweight trajectory prediction for embedded exists in autonomous driving (2025).
- **Why it matters for patent:** Trajectory-based threat intent estimation is not patented for edge NPU home security. Converts the system from reactive (weapon seen) to proactive (threat trajectory converging on person) — a qualitatively stronger and entirely different claim.
- **Feasibility:** Medium — compact LSTM over bounding-box centroids + pose keypoints from existing YOLO detections; runs on Core 1/2 alongside verification.
- **Implementation hint:** Track weapon centroid (x, y, area) and person keypoint velocity over 1–3 second window. Train 2-layer LSTM on "threat advancing" vs. "neutral carry" sequences. Threshold on threat-score time series.

---

### A3 — Lightweight Face Anti-Spoofing (Replaces MiDaS Variance Gate)
- **Problem:** MiDaS depth variance gate achieves AUC 0.563 on CelebA-Spoof — barely above chance. This is the single biggest admitted weakness in the existing system.
- **Current best:** MobileFaceNet-based anti-spoof for low-quality surveillance (Electronics/MDPI, 2024); M³FAS (arXiv 2301.12831, 2023) — RGB + depth fusion drives APCER below 0.1%; RGB-only MobileNet variants achieve >97% accuracy on LCC-FASD and SiW benchmarks.
- **Why it matters for patent:** Closes the biggest admitted weakness. Enables a specific claim about "multi-cue face liveness detection on NPU" replacing an AUC 0.563 gate.
- **Feasibility:** High — MobileFaceNet INT8 is under 2 MB, runs at >200 FPS on Hailo-8L. Sony IMX708 has sufficient resolution for texture analysis.
- **Implementation hint:** Train MobileFaceNet binary (real/spoof) on CelebA-Spoof + 500 in-domain captures (prints, phone replays). Compile to HEF. Run only when primary detector fires a Person detection.

---

### A4 — On-Device Continual Learning with Catastrophic Forgetting Prevention
- **Problem:** Deployed model is static. Cannot adapt to new environments (different lighting, new occupants, unusual objects) without cloud retraining.
- **Current best:** ETuner (arXiv 2401.16694, 2024) — 64% fine-tuning time reduction and 56% energy savings on edge; LightCL (arXiv 2407.10545, 2024) — 6.16× memory footprint reduction; LifeLearner (2023) validated on Raspberry Pi 3B+.
- **Why it matters for patent:** "Self-improving security system" claim. On-device continual learning is unpatented in edge home security. Directly addresses known Gap 11 (single-seed dataset limitation).
- **Feasibility:** Medium — Hailo does not support on-device training; only CPU-side classifier adaptation of MobileNetV2 verifier via LoRA is feasible.
- **Implementation hint:** Freeze YOLO backbone entirely. Apply LoRA adapters to MobileNetV2 last 2 layers. Maintain bounded replay buffer (200 samples). Trigger 50-iteration fine-tuning pass at 2AM daily on Core 2.

---

### A5 — Open-Set Anomaly Detection (Beyond Known 4 Classes)
- **Problem:** System only flags Hammer, Knife, Person, Scissors. Firearms, syringes, baseball bats — completely blind. Open-set detection entirely absent.
- **Current best:** "A Unified Survey on Anomaly, Novelty, Open-Set, and OOD Detection" (TMLR 2023); "From anomaly detection to open set recognition" (Pattern Recognition 2023); Edge-FLGuard (MDPI 2025) for IoT edge one-class classification; PMC 10857634 (2024) — smart camera anomaly detection under low-light.
- **Why it matters for patent:** A 4-class closed-world classifier is a known commercial limitation. Open-set detection flags "unknown threatening object" without retraining — applicable to arbitrary future threat categories.
- **Feasibility:** Medium — use MobileNetV2 penultimate feature vector as embedding; apply IsolationForest / OC-SVM on known-safe crops; runs on CPU, no Hailo change.
- **Implementation hint:** Extract 128-d embedding from MobileNetV2 verifier for all verified-safe training crops. Fit sklearn IsolationForest offline. At inference, score each verified crop; threshold triggers "Unknown Object — Review" alert with clip.

---

### A6 — ARM TrustZone Confidential Inference for Key and Model Protection
- **Problem:** AES-256 key and PBKDF2 credentials stored unprotected on Pi filesystem. Physical device access = full key recovery and model extraction.
- **Current best:** "Confidential Execution of Deep Learning Inference at the Untrusted Edge with ARM TrustZone" (ResearchGate, 2023) — CNN inference inside TEE on Cortex-A; Pi 5's Cortex-A76 supports TrustZone; 12–28% overhead per benchmarks.
- **Why it matters for patent:** "Cryptographically protected model weights and session keys via hardware TEE" is a completely novel claim in home security edge AI. Closes a real physical attack surface no current patent addresses.
- **Feasibility:** Medium — OP-TEE runs on Pi 4/5 under mainline Linux. Moving credential store and alert signing key to secure world is straightforward in 12 months.
- **Implementation hint:** Deploy OP-TEE on Pi 5. Store PBKDF2 salt + AES key in secure storage. Sign every alert clip with TEE-resident private key; recipient-verifiable for chain-of-custody.

---

### A7 — Cryptographic Chain-of-Custody for AI-Generated Evidence
- **Problem:** Alert clips are encrypted and uploaded but there is no verifiable proof they came from a specific device at a specific time and are untampered. Courts are beginning to require provable AI evidence chains (Louisiana enacted first AI evidence admissibility framework, August 2025).
- **Current best:** Quinn Emanuel legal analysis (2025); CDT inadmissibility analysis (2025); proposed Rule 707 (public comment through Feb 2026); UC Chicago Legal Forum deepfake concerns (2025).
- **Why it matters for patent:** "Tamper-evident, cryptographically signed alert clips with device attestation for legal admissibility" — unique in home security space. High commercial value for insurance and law enforcement use cases.
- **Feasibility:** High — SHA-256 hash + timestamp + device ID + Ed25519 signature is less than 1 week of implementation. The legal and patent value far exceeds the effort.
- **Implementation hint:** On every alert, compute SHA-256(clip_bytes + ISO8601_timestamp + device_uuid). Sign with Ed25519 key (stored in OP-TEE or software keystore). Embed in clip metadata. Ship a verification CLI tool.

---

### A8 — Physical Adversarial Patch Detection and Defense
- **Problem:** No defense against adversarial patches — printed stickers or clothing patterns designed to suppress YOLO detections. Thys et al. (CVPR 2019) showed patches can reduce person detection recall from ~90% to ~0%. Person recall is already only 0.609.
- **Current best:** PATCHOUT (Springer Neural Processing Letters, 2025) — semantic consistency checks; "Segment and Recover" (PMC, 2025) — patch-agnostic preprocessing defense; NeurIPS 2024 poster 96825 — camera-agnostic patch attack analysis.
- **Why it matters for patent:** Defense against physical adversarial attacks is a novel patent claim with direct commercial value for high-security installations. No prior home security patent addresses physical attacks.
- **Feasibility:** Medium — lightweight consistency verifier: if YOLO fires on person crop but MobileNetV2 returns <10% confidence on any class (extreme OOD), flag "possible adversarial patch." 1-day implementation for basic version.
- **Implementation hint:** Add consistency check: if YOLO detection + MobileNetV2 verifier returns extreme OOD embedding, flag "Possible Adversarial Patch — Alert Suppressed" and log raw frame separately for human review.

---

### A9 — Person Re-Identification Across Time Windows (Single-Camera)
- **Problem:** Each detection is independent. Person leaves and re-enters = new detection. System cannot count unique individuals, track dwell time, or re-trigger appropriately on the same person.
- **Current best:** "Real-time person re-ID and tracking on edge devices" (Pattern Analysis and Applications, Springer 2025); MICRO-TRACK (arXiv 2409.03879, 2024) — open-set industrial re-ID; "Comprehensive Deep Learning for Multi-Camera Person Re-ID" (ScienceDirect, 2025).
- **Why it matters for patent:** Enables "persistent threat tracking" — if a person with a weapon leaves and re-enters, system re-triggers immediately without full pipeline delay. Also enables dwell-time anomaly detection.
- **Feasibility:** High — OSNet-x0.25 (560 KB) compiles to HEF; circular gallery of last 50 person embeddings with cosine similarity lookup.
- **Implementation hint:** Compile OSNet-x0.25 to HEF. Maintain circular gallery of 50 person embeddings with timestamps. On new person detection, if cosine similarity to gallery entry >0.8, treat as re-entry and re-trigger appropriate alert mode.

---

### A10 — Low-Light Domain Adaptation for Night Detection
- **Problem:** Paper mentions "night mode" but provides zero evaluation at low lux. YOLOv8s trained on standard datasets degrades significantly in darkness. No low-light enhancement pipeline exists.
- **Current best:** "Learning Optimized Low-Light Image Enhancement for Edge Vision Tasks" (CVPR 2024 Workshop NTIRE); NTIRE 2024 Challenge — 30+ competitive methods; Two-stage enhance-then-detect pipeline (PMC 12190514, 2025); ELS-YOLO (PMC 12300599, 2025) for UAV low-light.
- **Why it matters for patent:** Night intrusion is the primary threat scenario. A system with no low-light evaluation is incomplete. Closes known Gap 2 (nuisance conditions).
- **Feasibility:** High — ZeroDCE++ is <1 MB, runs at >30 FPS on CPU, preprocesses frames before Hailo ingestion. Sony IMX708 Night Mode ISP tuning via libcamera is a free performance gain with no model change.
- **Implementation hint:** Step 1: Benchmark detection mAP at 0.1 / 1 / 10 / 100 lux. Step 2: Insert ZeroDCE++ preprocessing on CPU before HailoRT ingestion. Step 3: Test if Sony IMX708 Night Mode ISP suffices alone.

---

### A11 — Knowledge Distillation from Foundation Models to On-Device Detector
- **Problem:** YOLOv8s trained on author-curated 4-class data with single seed. CLIP and OWL-ViT can provide open-vocabulary pseudo-labels at zero annotation cost, improving generalization to unseen threat categories.
- **Current best:** LMM-guided knowledge distillation (Springer Journal of Cloud Computing, 2026); "Knowledge Distillation in Object Detection for Resource-Constrained Devices" (IEEE Access, 2024) — 100× inference speed-up; DETR-to-YOLO distillation preserves edge deployability.
- **Why it matters for patent:** CLIP-distilled YOLO student generalizes to unseen weapon categories without explicit annotation — "foundation-model-bootstrapped edge detector" claim. Directly addresses known Gaps 11 and 12.
- **Feasibility:** High — distillation training runs on MacBook M2 / cloud GPU offline; only the distilled INT8 HEF is deployed to Pi. No hardware change.
- **Implementation hint:** Use OWL-ViT on cloud GPU to generate bounding box pseudo-labels for 5,000 unlabeled surveillance frames covering novel categories (firearms, bottles, syringes). Merge with original dataset. Retrain YOLOv8s. Compile to HEF. Benchmark mAP on both original and novel categories.

---

### A12 — Differential Privacy for On-Device Adapter Training
- **Problem:** If continual learning (Gap A4) is added, raw gradients can leak training data through gradient inversion attacks. DP noise injection during adapter fine-tuning prevents frame reconstruction.
- **Current best:** NIST SP 800-226 (2024) — DP evaluation guidelines; "Federated learning with hybrid DP for cross-IoT" (Wiley Security and Privacy, 2024); DP co-design with energy-efficient encryption for edge (ScienceDirect AI survey, 2025).
- **Why it matters for patent:** "Differentially private on-device adapter training for privacy-preserving continual learning" — novel combination claim (DP + CL + edge NPU + home security). Closes the gradient privacy gap that would otherwise make Gap A4 a liability.
- **Feasibility:** High — DP-SGD is a 50-line addition using Opacus library. Epsilon calibration on small LoRA adapters is computationally trivial.
- **Implementation hint:** Use Opacus DP-Adam when running nightly adapter fine-tuning. Set epsilon=8, delta=1e-5. Log privacy budget consumption per round. Surface in web dashboard as a privacy accountability metric.

---

### A13 — Neural Network Model Watermarking for IP Protection
- **Problem:** HEF files on the Pi have no ownership proof. Physical device access allows model extraction and commercial redeployment.
- **Current best:** "Securing IP in Edge AI: Neural Network Watermarking for Multimodal Models" (Applied Intelligence / Springer, 2024); PK-Judge (MDPI Big Data, 2025) — asymmetric crypto for ownership verification; Deep Serial Number (ECML-PKDD 2023).
- **Why it matters for patent:** Patent protection covers system design; watermarking adds a layer that survives legal challenges. A watermark-bearing model is provably the inventor's model even after fine-tuning or extraction.
- **Feasibility:** High for MobileNetV2 verifier (full PyTorch weights accessible). Medium for YOLO HEF (compilation may strip watermarks — needs testing).
- **Implementation hint:** Embed backdoor watermark (trigger pattern → fixed output) in MobileNetV2 during training. Document trigger, expected output, and hash in a notarized record. Verify watermark survives INT8 quantization.

---

### A14 — Structured Pruning + Early-Exit for Dynamic Power Scaling
- **Problem:** System runs at constant 5.89 W regardless of scene activity. During nighttime idle, the same compute is consumed as during active intrusion.
- **Current best:** E4 (arXiv 2503.04865, 2025) — combines early-exit + DVFS for significant energy savings on edge video analytics; "Early-Exit meets MDI" (IEEE/arXiv 2408.05247, 2024); ACM Computing Surveys early-exit DNN survey (2024).
- **Why it matters for patent:** Below 4 W during idle enables battery backup, lower thermal stress, and a specific "adaptive power budget" claim.
- **Feasibility:** Medium — HEF compilation does not natively support mid-model exits; requires a two-model approach (tiny pre-filter + full YOLO on positives only).
- **Implementation hint:** Deploy MobileNetV1 0.25× (<500 KB) as motion + foreground pre-filter on Hailo. When pre-filter confidence <0.15, skip full YOLO and sleep Hailo for 100 ms. Measure mean power reduction under synthetic idle workload.

---

### A15 — Weakly Supervised Video Anomaly Detection as Second Alert Layer
- **Problem:** System only fires on trained weapon/person classes. Cannot detect loitering, unusual motion patterns, aggressive gestures, unusual object placement — anything not in 4 training classes.
- **Current best:** VadCLIP (2024) — first CLIP transfer to weakly supervised video anomaly detection; "Benchmarking Compact VLMs for Clip-Level Surveillance Anomaly Detection" (PMC, 2025); "Anomalies are Streaming: Continual Learning for WSVAD" (OpenReview, 2025).
- **Why it matters for patent:** A second independent alert pathway (anomaly detection) not dependent on trained classes provides coverage for novel threats. Directly addresses known Gaps 7 (temporal activity) and 12 (open-set evaluation).
- **Feasibility:** Medium — compact VAD (MNAD or AnomalyClip-distilled) on CPU using 2-second clip windows; one-class reconstruction model (autoencoder on normal frames) is a 2-week implementation.
- **Implementation hint:** Train convolutional autoencoder on 48 hours of normal scene footage (available from soak test). At inference, compute reconstruction error on 2-second windows. If error >3σ above scene-specific mean, trigger "Unusual Activity" secondary alert.

---

### A16 — Scene Graph / GNN Context-Aware Alert Scoring
- **Problem:** System fires on object classes without spatial context. Knife near cutting board (kitchen, daytime) should have different alert priority than knife in bedroom at 3AM.
- **Current best:** GNNs for scene understanding (Springer, 2025); SCENE — heterogeneous GNN for traffic; GNN-based intrusion detection (Computers and Security 2024); TE-G-SAGE (MDPI 2025) — explainable graph reasoning on edge.
- **Why it matters for patent:** "Context-aware threat scoring using spatial relationship graphs" — reduces false positives from benign weapon presence (kitchen knives, hardware tools). No current home security patent uses spatial reasoning.
- **Feasibility:** Medium — 2-layer GraphSAGE (<100 KB) over detected objects as nodes and spatial relationships as edges; runs on CPU in <5 ms; requires ~200 annotated scenes for training.
- **Implementation hint:** Build scene graph per frame: nodes = detected bounding boxes (class + confidence), edges = pairwise spatial relationships. Feed into 2-layer GraphSAGE. Output context-adjusted threat score. Train on 200 hand-labeled frames.

---

### A17 — Federated Learning Across Multiple Deployed Devices
- **Problem:** Each deployed device learns nothing from other instances. 100 deployed Garudas could collaboratively improve the shared model without any device sharing raw video.
- **Current best:** "Federated Learning for Privacy-Preserving Edge AI" (ResearchGate, 2025); Edge-FLGuard (MDPI 2025); "Federated Continual Learning for Edge-AI: A Comprehensive Survey" (arXiv 2411.13740, 2024); "A Review on Federated Learning Architectures" (Electronics/MDPI, 2025).
- **Why it matters for patent:** Expands the patent from a single-device system to a networked learning system. Federated learning for edge AI home security is unpatented. Market projected at $297.5M by 2030.
- **Feasibility:** Low for production; Medium for research prototype. Flower (flwr) framework runs on Pi 5; only MobileNetV2 adapter weight deltas shared (not video).
- **Implementation hint:** Use Flower with Pi 5 as a client. Share only LoRA adapter weight deltas (<50 KB per round) encrypted with existing AES-256-CBC key. Federated server on any cloud VPS. Demonstrate 3 simulated clients improving shared adapter over 10 rounds.

---

### A18 — Thermal + RGB Sensor Fusion for 24/7 All-Conditions Detection
- **Problem:** Sony IMX708 is an RGB camera. In complete darkness (0 lux), thermal imaging is required for reliable detection. System has no true zero-lux operation plan.
- **Current best:** PitFusion (Ivmech Mechatronics, 2025) — MLX90640 thermal + Pi camera fusion; Arducam RGBD ToF Camera Kit on Pi via MIPI CSI; Pi camera thermal overlay (Hackaday.io, 2024).
- **Why it matters for patent:** True 24/7 surveillance requires all-light-condition detection. Thermal + RGB fusion on same Hailo hardware is a novel claim. Thermal also provides a second face liveness cue (live faces are warm; photos are not).
- **Feasibility:** High for thermal pre-filter (MLX90640 via I2C, ~$15 BOM addition). Full thermal-guided detection fusion is Medium.
- **Implementation hint:** Add MLX90640 thermal via I2C. At night, run full Hailo pipeline only when thermal detects warm region (>2°C above ambient). Cuts nighttime false triggers from ambient motion (curtains, shadows).

---

### A19 — Quantization-Aware Training for Accuracy Recovery
- **Problem:** Current INT8 HEF was compiled from FP32 YOLOv8s checkpoint via post-training quantization. PTQ typically incurs 1–4 mAP points accuracy loss, especially on small objects. QAT gap is uncharacterized in the paper.
- **Current best:** Hailo Model Zoo provides PTQ pipelines but no QAT workflow; "Quantized Image Super-Resolution on Mobile NPUs" (CVPR 2025 MAI) — QAT recovers 2–3 dB lost by PTQ; "Pruning + Quantization integration" (arXiv 2509.04244, 2025).
- **Why it matters for patent:** Measurable improvement claim: if QAT recovers person recall from 0.609 to >0.70, that is a concrete, verifiable improvement with the same hardware and no BOM change.
- **Feasibility:** High — QAT runs on MacBook M2 (MPS backend) or cloud GPU; only the recompiled HEF is deployed to Pi.
- **Implementation hint:** Step 1: Measure float32 mAP on test set to establish PTQ gap. Step 2: Run QAT with fake-quantization in PyTorch for 10 epochs. Step 3: Recompile to HEF. Step 4: Re-measure. Target: person recall improvement from 0.609 to >0.70.

---

### A20 — Network-Level NIDS (LAN Spoofing, Replay, MITM Defense)
- **Problem:** ARP spoofing can silence SSH alert exfiltration. DNS poisoning can redirect Cloudflare tunnel. TLS downgrade and replay attacks on alert channel are untested and undefended. Paper admits this (Gap 4) but provides no solution.
- **Current best:** "Enhancing IoT IDS through Adversarial Training" (arXiv 2507.19739, 2025); IoT attacks surged 107% in early 2024, routers = 75% of IoT breaches (MDPI Sensors, 2024); FGSM-adversarially-trained models — 94.5% detection accuracy under adversarial conditions; "Ensemble Technique for IoT-Edge Intrusion Detection" (Scientific Reports, 2024).
- **Why it matters for patent:** A security system that can be silenced by ARP spoofing provides no real security guarantee. A co-deployed NIDS is a novel claim with high commercial value for high-security installations.
- **Feasibility:** High — lightweight ML-NIDS (gradient-boosted tree, <1 MB) monitoring Pi's own network interface for ARP/DNS/TCP anomalies runs on Core 3 at <0.3 W additional power. Suricata as baseline rule engine also works on Pi 5.
- **Implementation hint:** Run Suricata in lightweight mode on Core 3 monitoring eth0. Add 5 custom rules: ARP spoofing, DNS response anomaly, SSH connection refusal spike, Cloudflare tunnel disconnect without reconnection, SMTPS TLS downgrade. Fallback alert via Twilio SMS.

---

## 12 Known Gaps Already Acknowledged in the Paper

| # | Gap | Novel Solution |
|---|---|---|
| 1 | No alert precision/recall over continuous deployment (>48 h) | Baseline experiment — measure over 2 weeks |
| 2 | No nuisance-condition sweep (lighting, glare, blur, occlusion) | A10 (low-light pipeline), A18 (thermal) |
| 3 | MiDaS depth variance gate AUC 0.563 — admitted weakness | **A3 (real face anti-spoof model)** |
| 4 | No stress test under packet loss, ARP spoofing, tunnel drops | **A20 (NIDS)** |
| 5 | No matched-stimulus comparison vs Ring/Nest/Arlo | Structured experiment |
| 6 | No multi-week stability study (only 48 h telemetry) | Baseline experiment — run 2–4 weeks |
| 7 | No temporal activity recognition — system is per-frame only | **A2 (trajectory), A15 (video anomaly detection)** |
| 8 | No face anti-spoof stage on in-domain data | **A3** |
| 9 | Person recall weak (0.609) — crowded/small/occluded | **A19 (QAT), A11 (foundation model distillation)** |
| 10 | Voice assistant not validated (no mic in soak test) | Functional test with microphone attached |
| 11 | Single-seed, single-split dataset — no external benchmark | **A11 (foundation model distillation + new categories)** |
| 12 | Open-set verifier evaluation only 301 household clutter crops | **A5 (open-set anomaly detection)** |

---

## Priority Ranking for 12-Month Implementation Window

| Priority | Gap | Effort | Patent Value | Notes |
|---|---|---|---|---|
| 1 | A3 — Real Face Anti-Spoof | Low | High | Closes biggest admitted weakness (AUC 0.563 → >95%) |
| 2 | A7 — Chain-of-Custody Signing | Low | High | Legal admissibility; <1 week to implement |
| 3 | A10 — Low-Light Pipeline | Low | High | ZeroDCE++ on CPU; free improvement via IMX708 Night Mode ISP |
| 4 | A19 — QAT Accuracy Recovery | Low | High | Person recall 0.609 → >0.70; MacBook M2 training |
| 5 | A1 — Audio-Visual Fusion | Medium | High | Novel modality claim; USB mic + YAMNet on Core 3 |
| 6 | A8 — Adversarial Patch Defense | Medium | High | Closes physical attack surface |
| 7 | A9 — Person Re-ID | Medium | Medium | OSNet-x0.25 HEF; persistent tracking claim |
| 8 | A20 — Network NIDS | Medium | High | Suricata + custom rules; closes Gap 4 |
| 9 | A5 — Open-Set Anomaly Detection | Medium | High | IsolationForest on MobileNetV2 embeddings |
| 10 | A15 — Weakly Supervised Video Anomaly | Medium | High | Autoencoder on 48 h soak footage |
| 11 | A6 — ARM TrustZone TEE | Medium | High | OP-TEE on Pi 5; protect keys and clip signing |
| 12 | A4 — Continual Learning (LoRA) | Medium | High | Nightly LoRA adapter fine-tuning on Core 2 |
| 13 | A12 — Differential Privacy | Low | Medium | Opacus DP-Adam; companion to A4 |
| 14 | A14 — Early-Exit Power Scaling | Medium | Medium | Tiny pre-filter + sleep Hailo on idle |
| 15 | A16 — Scene GNN Context Reasoning | Medium | Medium | 2-layer GraphSAGE; 200 labeled frames |
| 16 | A2 — Trajectory Prediction | Medium | High | 2-layer LSTM over centroid + keypoint velocity |
| 17 | A11 — Foundation Model Distillation | Low | High | OWL-ViT pseudo-labels; MacBook M2 training |
| 18 | A18 — Thermal + RGB Fusion | Medium | High | MLX90640 via I2C; $15 BOM addition |
| 19 | A13 — Model Watermarking | Medium | Medium | Backdoor watermark in MobileNetV2 |
| 20 | A17 — Federated Learning | High | High | Flower framework; long-term commercial claim |

---

## Key Sources (2022–2025)

- Multimodal anomaly detection (Scientific Reports, 2025) — audio-visual fusion
- M³FAS multi-modal face anti-spoof (arXiv 2301.12831, 2023)
- MobileFaceNet face anti-spoof for low-quality surveillance (Electronics/MDPI, 2024)
- ETuner edge continual learning (arXiv 2401.16694, 2024)
- LightCL low-memory continual learning (arXiv 2407.10545, 2024)
- "Unified Survey on Anomaly, Novelty, Open-Set, OOD Detection" (TMLR, 2023)
- "Confidential Execution of DL Inference with ARM TrustZone" (ResearchGate, 2023)
- AI evidence admissibility — Quinn Emanuel analysis (2025); CDT (2025); proposed Rule 707
- PATCHOUT adversarial patch detection (Springer Neural Processing Letters, 2025)
- Thys et al. adversarial patches vs. person detection (CVPR, 2019)
- NeurIPS 2024 poster 96825 — camera-agnostic adversarial patch analysis
- OSNet person re-ID (ICCV, 2019); real-time edge re-ID (Springer, 2025)
- ZeroDCE++ low-light enhancement — CVPR 2021; edge vision task optimization CVPR 2024
- LMM-guided knowledge distillation (Springer Journal of Cloud Computing, 2026)
- Knowledge Distillation in Object Detection for edge (IEEE Access, 2024)
- E4 early-exit + DVFS (arXiv 2503.04865, 2025)
- "Early-Exit meets MDI" (arXiv 2408.05247, 2024)
- VadCLIP weakly supervised video anomaly (2024); Benchmarking Compact VLMs for VAD (PMC, 2025)
- GNN for intrusion detection survey (Computers and Security, 2024)
- TE-G-SAGE explainable GNN on edge (MDPI, 2025)
- Federated Continual Learning for Edge-AI (arXiv 2411.13740, 2024)
- Flower federated learning framework (flwr.dev)
- PitFusion thermal + Pi camera (Ivmech, 2025); MLX90640 I2C on Pi
- "Securing IP in Edge AI: NN Watermarking" (Applied Intelligence / Springer, 2024)
- DP-SGD Opacus library; NIST SP 800-226 (2024) DP evaluation guidelines
- "Enhancing IoT IDS through Adversarial Training" (arXiv 2507.19739, 2025)
- Ensemble IoT-Edge Intrusion Detection (Scientific Reports, 2024)

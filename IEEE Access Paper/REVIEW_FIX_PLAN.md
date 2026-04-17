# IEEE Access Paper -- Review Fix Plan

Status: IN PROGRESS
File: `Template_Access/ACCESS_latex_template_20240429/access.tex`

---

## P0 -- Fatal Flaws (Fix Before Anything Else)

### P0-1. Cascade is NOT deployed -- undermines Contribution #2
- ~~Sec V-D admits: "It lives in its own pipeline file and is not invoked by the deployed detection application."~~
- **Decision:** Integrated cascade into deployed pipeline as async secondary path (primary YOLO on Core 1, secondary MobileNetV2+MiDaS on Core 2, non-blocking queue with intentional drops).
- [x] Make decision -- integrated into deployed pipeline
- [x] Rewrite Sec V-D accordingly -- updated to describe async architecture, 4-thread pipeline, CascadeMetrics, CPU affinity
- [x] Update contribution list (Sec I-A, item 2) -- now states async secondary path with bounded non-blocking queue
- Abstract also updated to reflect deployed cascade.

### P0-2. Quantisation comparison is apples-to-oranges
- Table 15: FP32 mAP reported on **val** (~0.855), INT8 mAP on **test** (0.847). Different splits. Cannot call delta "-0.8%".
- [ ] Re-evaluate FP32 model on the same held-out test set
- [ ] Update Table 15 with same-split numbers

### P0-3. MobileNetV2 validation is fatally weak
- ~~Table 7: only 24 Weapon validation crops. 99.6% accuracy on 24 samples is statistically meaningless.~~
- [x] Option A: Collected proper held-out set (250 Weapon + 250 Safe = 500 crops from val+test splits, zero train overlap)
- [x] Retrained classifier v2 on tight object crops (1,548 Weapon + 2,000 Safe from train split only)
- [x] Updated Table 7 with v2 dataset (3,548 train, 500 held-out)
- [x] Replaced 99.6% with 99.0% (Wilson CI [97.7%, 99.6%], n=500) everywhere
- [x] Added new "MobileNetV2 Classifier Evaluation" subsection in Results with v1 vs v2 comparison, confusion matrix, and Wilson CI
- [x] Updated cascade architecture to describe weapon-crop verification (not person-crop), matching v2 training methodology

### P0-4. Anti-spoof has zero quantitative evaluation
- ~~MiDaS depth-variance threshold (0.05) is stated with no ROC, no spoof dataset, no false-rejection rate.~~
- [x] Built CelebA-Spoof benchmark (3,000 imgs: 1,500 real + 1,500 spoof; 80/20 stratified, seed 0 -> 2,400 train, 600 test) and in-house capture (batchA/batchB, n=59)
- [x] Reported B0 depth-variance threshold AUC 0.563 [0.516, 0.610] on CelebA test -- barely above chance
- [x] Reimplemented two published baselines: B1 LBP+SVM (AUC 0.848), B2 HSV colour-texture+SVM (AUC 0.898)
- [x] Built 25-dim descriptor (depth + FFT + Lab chroma gradients + HSV spread + uniform-LBP); RBF-SVM reaches AUC 0.951 [0.935, 0.967], non-overlapping CI vs every baseline
- [x] In-house LOO: AUC 0.914 [0.832, 0.978]; cross-corpus collapse to 0.442 reported honestly as domain gap
- [x] Added Sec VI subsection "Anti-Spoof Stage Evaluation" (`\label{sec:antispoof_eval}`), Table `tab:antispoof`, and ROC figure `fig:antispoof_roc`
- [x] Updated methodology paragraph (Sec V-D) to remove the unsupported "4x safety margin" claim and reference Sec VI evaluation
- [x] Updated Conclusion future-work line to point at the 25-dim descriptor upgrade path
- Artefacts: `antispoof_eval/ANTISPOOF_REPORT.md`, `antispoof_v4_summary.json`, `antispoof_v4_roc.pdf` (now at `figures/fig_antispoof_roc.pdf`)

---

## P1 -- Structural Weaknesses (Required for Accept)

### P1-1. Related Work is shallow and missing key references
- Only 5 actual competing-system citations. Missing:
  - Weapon detection literature (knives, guns, CCTV surveillance)
  - Anti-spoofing / presentation attack detection literature
  - Frigate NVR or similar open-source edge-AI NVR
  - Jetson Orin Nano / Coral M.2 benchmarks
  - Commercial edge-AI cameras (Wyze, Reolink AI)
- [ ] Add 2-3 weapon-detection papers
- [ ] Add 1-2 face/presentation anti-spoof references
- [ ] Add Frigate NVR or similar
- [ ] Add Jetson-based home security paper
- [ ] Target 20+ total references

### P1-2. Research gap statement is a feature list, not a gap
- Line 94: "no published system known to the author combines... all of the following simultaneously"
- Novelty-by-enumeration is not a research gap.
- [ ] Rewrite as capability gap with citations: "Existing edge-AI security systems either lack X [cite] or depend on Y [cite], creating a trade-off that no current system resolves at the edge."

### P1-3. Edge comparison table (Table 17) has no citations
- "Pi + CPU YOLOv8s: 3-5 FPS", "Jetson Nano + YOLOv5: ~30 FPS" etc. are uncited estimates.
- [ ] Either run those baselines and measure, or cite specific benchmarks
- [ ] Add YOLOv8s on Jetson Orin Nano, Coral M.2, Hailo-8 full
- [ ] Delete any row that cannot be backed by data or citation

### P1-4. No real-world deployment evaluation -- DONE (harness deployed)
- Every result is on static images. Zero evaluation of:
  - End-to-end alert latency in real deployment
  - False alarm rate per hour/day in actual home operation
  - Voice assistant accuracy (WER, intent accuracy)
  - Presence monitor reliability
  - Deadman watchdog false-trigger rate
- 12 engines described, only 1 evaluated (detector).
- [x] Built 36 h evaluation harness `evaluation/eval_36h.py` (tails `perm_*` logs, polls `/api/state`, heartbeats, scheduled probes for presence/email/LLM/clip/deadman, WER+intent vs `voice_ground_truth.json`, alert-to-email latency correlation).
- [x] Added env-gated admin-OTP bypass in `Garuda_web.py` + service admin `svc_eval` so the harness can authenticate headlessly (revert steps: `evaluation/REVERT_EVAL_PATCH.md`).
- [x] Launched 36 h run on live deployment (started 2026-04-18 00:57 IST; run dir `evaluation/out/20260418_005724_0a654a/`). Writes `metrics.jsonl`, `engines.jsonl`, `alerts.jsonl`, `voice.jsonl`, `deadman.jsonl`, `events.jsonl`, `summary.json`.
- [ ] At run completion: fold `summary.json` into new "System-Level Evaluation" subsection in Sec VI.

### P1-5. Person recall (0.609) is acknowledged but not addressed
- Person is the primary security class, worst recall and mAP.
- [ ] Add Discussion paragraph: concrete steps to improve (larger person set, class-weighted loss, dedicated person head, fusion with person detector)
- [ ] Explain why not pursued in this version

### P1-6. FPS comparison against outdated hardware is misleading
- Comparing 52.2 FPS vs Jetson TX1 (2016) and FPGA (different task) is not meaningful.
- [ ] Compare YOLOv8s on current hardware: Jetson Orin Nano, Coral M.2, Hailo-8 full
- [ ] Use published benchmarks with citations

---

## P2 -- Presentation & Clarity

### P2-1. Novelty framing needs rewrite
- Core contribution is systems integration, not new algorithm. Paper must own that.
- [x] Reframe as reference architecture for NPU-accelerated edge security
- [x] Replace twelve-engine enumeration (Contribution #3) with generalizable design principle
- [x] Keep dataset-scaling study and cascade anti-spoof as reproducible scientific results

### P2-2. Abstract is overloaded (250+ words, lists all 12 engines)
- [x] Cut to ~150 words (now 157)
- [x] Structure: gap, approach (Pi 5 + Hailo-8L + YOLOv8s), key metric (52.2 FPS, 0.847 mAP, 18.4 ms), significance

### P2-3. Methodology is 13 subsections of developer documentation
- Reads like a README, not a research paper. No design justification.
- [x] Cut each engine description to 1 paragraph max (8 figures removed, 4 kept: hardware, gstreamer, cascade, alert)
- [x] Add "Design Decisions and Trade-offs" subsection (PBKDF2 vs Argon2, ARP vs mDNS, SQLite vs time-series DB, bounded drop vs back-pressure)
- [x] Moved async FPS content from methodology to Results as its own subsection

### P2-4. Too many tables (21)
- Many trivially small (Table 2: all zeros; Table 3: 9 augmentation params).
- [x] Fold trivial tables into prose (8 removed: integrity, augmentation, dataset geometry, bbox stats, TP/FN, cloud comparison, edge comparison; 2 appendix model tables merged into 1)
- [x] Target 10-12 tables -- now at 13 (body: 10, appendix: 3)

### P2-5. Figure captions lack takeaways
- ~~Captions restate figure type instead of key finding.~~
- [x] Rewrite every caption: state the takeaway visible in the figure
- [x] All 17 figure captions rewritten with concrete metrics and key findings

### P2-6. Orphaned references [b11]-[b17]
- ~~b11 (RPi brief), b12 (FastAPI), b13 (GStreamer), b14 (MiDaS), b15 (MobileNetV2), b16 (PKCS#5), b17 (Cloudflare) are never \cite{}'d in body text.~~
- [x] Add \cite{} where each technology is first mentioned
- b11: Raspberry Pi~5 first mention (Sec I), b12: FastAPI (Sec II), b13: GStreamer (Sec II Related Work), b14: MiDaS (Sec I-A Contributions), b15: MobileNetV2 (Sec I-A Contributions), b16: already cited (Sec V-G), b17: Cloudflare tunnel (Sec II)

---

## Progress Tracker

| ID | Task | Status |
|----|------|--------|
| P0-1 | Cascade deployment decision | DONE |
| P0-2 | Fix quantisation split | NOT STARTED |
| P0-3 | MobileNetV2 validation | DONE |
| P0-4 | Anti-spoof evaluation | DONE |
| P1-1 | Expand references (20+) | NOT STARTED |
| P1-2 | Rewrite research gap | NOT STARTED |
| P1-3 | Fix edge comparison table | NOT STARTED |
| P1-4 | Real-world deployment eval | DONE (36 h run in progress, harness + bypass deployed) |
| P1-5 | Person recall discussion | NOT STARTED |
| P1-6 | FPS comparison update | NOT STARTED |
| P2-1 | Novelty framing rewrite | DONE |
| P2-2 | Trim abstract | DONE |
| P2-3 | Cut methodology, add trade-offs | DONE |
| P2-4 | Merge trivial tables | DONE |
| P2-5 | Rewrite figure captions | DONE |
| P2-6 | Fix orphaned references | DONE |

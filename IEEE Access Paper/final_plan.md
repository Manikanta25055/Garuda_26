# Final Audit & Action Plan — Project Garuda IEEE Access Submission

Audit conducted as a strict IEEE Access reviewer on commit `3191956` (post-fig-14/15 paper, 20 pages, 5.8 MB). Items are ordered by **rejection risk**, not by paper order. Each item names the exact paper location and the change required.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

---

## P0 — Numerical inconsistencies a reviewer will catch immediately

These are arithmetic / cross-reference mismatches between the body text, tables, and figures. Each one undermines the credibility of every other number in the paper. Fix all five before resubmission.

### F1. Table 1 test-column sum is wrong
- **Where:** Section IV-A, Table 1 ("Image Counts Contributed by Each Source").
- **Issue:** Test column reads 258 + 14 + 56 + 245 + 35 = **608**, but the "All sources" row claims **622**.
- [ ] Either correct the per-row test counts so they sum to 622, or correct the total. The 622 figure is load-bearing — it appears in Table 9 caption, Sec. VI-J, the FP32 evaluation, and the INT8 evaluation, so the row counts must be reconciled to it (not the other way around).

### F2. Fig. 3 bars contradict Table 2 instance counts
- **Where:** Section IV-C, Fig. 3 (Dataset Split Distribution by Class) vs Table 2 (Annotated Instance Counts per Class per Split).
- **Issue:** Fig. 3 reports "Total 5,311 instances" with bars Hammer 480/68/30, Knife 860/122/60, Person 2100/302/173, Scissors 900/128/88. Table 2 reports total **16,335** instances with completely different per-class numbers (Hammer 254/35/46, Knife 1381/162/159, Person 10460/1273/1324, Scissors 981/133/127). The figure was clearly generated from an earlier dataset version or a different counting convention.
- [ ] Regenerate Fig. 3 from the same source as Table 2, OR clarify in the caption what "instances" means in each (e.g., images-with-class vs annotation-instances). If they really are different quantities, label the figure axis explicitly so the reader cannot read them as inconsistent.

### F3. Contributions bullet 1 has the wrong v2 image count
- **Where:** Section I-A, bullet 1: *"three annotation passes (359, 1,519, and 4,982 training images)"*.
- **Issue:** Every other reference to v2 in the paper says **1,383** (Sec. VI-C, Sec. VI-N, Fig. 11, Fig. 19, Table 11). The "1,519" in the contributions bullet is a typo / stale number.
- [ ] Replace `1,519` with `1,383` in the contributions bullet. Single-character fix.

### F4. Fig. 13 per-class mAP@0.5:0.95 disagrees with Table 7
- **Where:** Section VI-E, Fig. 13 (per-class detection performance) vs Table 7 (full per-class scorecard).
- **Issue:** Fig. 13 shows Person mAP@0.5:0.95 ≈ **0.41**; Table 7 reports **0.373**. Reading the bar values: Hammer 0.72 ≈ Table 7's 0.720, Knife 0.72 ≈ 0.721, Person 0.41 vs 0.373 (Δ = 0.04), Scissors 0.83 ≈ 0.828. The Person bar is from a different run than the table.
- [ ] Regenerate Fig. 13 from the same evaluation that produced Table 7, or relabel the table value if 0.41 is in fact the canonical number.

### F5. Dashboard caption says "NPU temp 73 °C" — contradicts the +3.8 °C thermal claim
- **Where:** Fig. 8 (operator dashboard, Sec. V-G) vs Sec. VI-J ("NPU surface-temperature rise of only +3.8 °C above ambient idle").
- **Issue:** A reviewer reading the caption will compute: ambient ~25 °C + 3.8 °C = 28.8 °C, but the dashboard shows 73 °C labelled "NPU temp". Either the +3.8 °C claim is wrong, or the dashboard is showing **SoC** (CPU/Pi 5 chip) temperature, not the Hailo-8L NPU temperature. The Pi 5 CPU running at 2.8 GHz with the active cooler genuinely sits around 70–75 °C under load, while the Hailo-8L is much cooler. They are different sensors.
- [ ] In Fig. 8 caption, relabel "NPU temp 73 °C" → "SoC temp 73 °C" (or "CPU temp"). Add a one-line note in Sec. VI-J distinguishing the two thermal channels: SoC temperature (CPU package, ~73 °C under sustained load) versus Hailo-8L NPU temperature (which is the +3.8 °C measurement).

---

## P1 — Open Tier 1/2 items a reviewer will re-raise as the original objection

These are the items the previous reviewer flagged that are still not closed; the next reviewer will repeat them verbatim.

### F6. Seed-variance study (originally Tier 1, item 4) — still open
- **Where:** Section IV-A "Single split, single seed" disclosure; abstract trajectory `0.465 → 0.754 → 0.847`.
- **Issue:** The previous review fix added the disclosure but did not run variance. A strict reviewer will note: "you acknowledge the limitation but still publish the point estimates as the headline trajectory." The disclosure does not absolve the headline.
- **Options (pick one):**
  - [ ] **A — Run it.** 9 fine-tunes (v1, v2, v5 × 3 seeds each) on a RunPod A100. ~6–8 h overnight. Replace point estimates in Table 11 / Fig. 19 / Fig. 20 / abstract with mean ± stdev.
  - [ ] **B — Strengthen the disclosure.** Move the limitation from Sec. IV-A into the abstract and Conclusion (currently only in Sec. IV-A and Sec. VII, easy to miss). Cite a published seed-variance study on YOLO fine-tuning to bracket the expected variance (e.g., ±0.01–0.02 mAP for v5-scale corpora). Reframe the trajectory as "an existence proof of within-corpus scaling on this dataset, not a generalisable scaling law."
- Recommendation: **option A** if the paper is being submitted within a week and a pod is available; **option B** otherwise. Option A closes the objection cleanly; option B asks the reviewer to accept a soft acknowledgement.

### F7. Latency distributions (originally Tier 2, item 8) — still open
- **Where:** Table 4 "Secondary Verifier Path: Per-Stage Latency Budget".
- **Issue:** Table 4 still uses inequalities and approximations: `~1 ms`, `<2 ms`, `~5 ms`, `~0.5 ms`, `<0.1 ms`, `~800 ms`, `~200 ms`, `~22 ms`. The previous reviewer specifically called these out as imprecise. They are still imprecise.
- [ ] Patch `garuda_cascade.py` to log per-stage timestamps (one line per inference) to a CSV; let the running 36-h evaluation collect ≥1000 samples per stage; replace each inequality with `mean ± stdev` and `p50 / p95 / p99`. The instrumentation is non-disruptive (no service restart needed if added to a hot-reload section, otherwise a single restart).
- Optional alternative if instrumenting the live cascade is too invasive: run a 5-minute standalone trace on the recorded `clip_1776278266.mp4` test clip and report distributions from that.

### F8. FPS-flatness load is lighter than the originally specified stress test
- **Where:** Section VI-I "Sustained FPS Across Operating Modes", Fig. 17.
- **Issue:** Tier 1 item 3 in the revision plan specified the concurrent load as "WebRTC stream + 5 SMTP alerts + 3 SSH clip uploads + voice query". The current trace runs only "9 danger triggers and 3 voice-assistant queries". A strict reviewer comparing to the previous review will notice that the SMTP and SSH clip-upload paths — the two slow channels the asynchronous architecture exists to absorb — were not exercised in the published trace. The 52.01 ± 1.44 FPS number therefore measures the system under inference-side load only, not under the full deployment-realistic concurrent load.
- [ ] Either: (a) re-run the trace with 5 real SMTP alerts and 3 real SSH clip uploads injected during the Active and Emergency windows, regenerate Fig. 17 and update the headline number; or (b) shrink the claim — explicitly state in Sec. VI-I that the load was inference-side triggers + voice and that SMTP/SSH paths were exercised separately (cite Sec. V-E for the alert-engine asynchrony argument). Option (a) is the cleaner fix.

---

## P2 — Authorship / framing issues that invite reviewer scepticism

### F9. Author Contributions: second author has only review/supervision roles
- **Where:** Author Contributions block (page 19).
- **Issue:** The CRediT statement assigns G. V. Manikanta everything (conceptualisation, methodology, software, hardware, dataset, training, compilation, all measurements, figures, draft, revisions). S. Tangi gets "methodology review, validation of the experimental protocol, writing review and editing, and supervision." Two structural problems: (i) "supervision" is unusual when both authors share the same affiliation and are at the same career stage (the listed affiliation is a single department, no faculty/student distinction is given); (ii) the listed second-author roles are all observational — none is independent generative work. A strict reviewer will read this as an honesty-driven CRediT statement that, by its very honesty, does not justify dual authorship.
- [ ] Either: (a) expand S. Tangi's contribution honestly if there is generative work missing from the current statement (e.g., specific subsystem implementation, specific dataset annotation, specific experimental run); or (b) drop S. Tangi to Acknowledgements and submit as single-author. Option (a) is preferred; option (b) is a cleaner ethical position than the current CRediT statement.

### F10. Defensive sentence about the failed emulator run can be cut
- **Where:** Section VI-J, second-to-last sentence of the methodology paragraph: *"An earlier emulator-based INT8 evaluation attempted on a re-optimised HAR with academic NMS thresholds did not produce a usable mAP because the SDK_QUANTIZED bbox output layout did not match the assumed bbox-decoder convention; running the production HEF on the deployed Hailo-8L (rather than the x86 emulator) avoided that issue entirely and is also the more deployment-relevant measurement."*
- **Issue:** Now that the on-device measurement is in the paper, narrating a failed earlier attempt invites the reviewer to ask "why was this in the paper at all?" The sentence reads as defensive.
- [ ] Cut the sentence. The on-device methodology stands on its own.

---

## P3 — Polish (low-impact but worth doing)

### F11. Acronym hygiene
- **Where:** Throughout.
- [ ] FAR (False Alarm Rate) is defined in Table 7 footnote but used earlier in body text — define on first use in Sec. VI-E.
- [ ] CRediT (in Author Contributions) — spell on first use as "Contributor Roles Taxonomy (CRediT)".
- [ ] PMIC (in Sec. VI-J power paragraph) — spell as "Power Management IC (PMIC)".

### F12. Conclusion future-work compression
- **Where:** Sec. VII Conclusion, last three paragraphs (in-situ end-to-end security; generalisability re-analysis; component upgrades).
- **Issue:** Three separate future-work paragraphs at the end of a Conclusion is structurally heavy.
- [ ] Fold the three paragraphs into a single tight paragraph. The detail is already in the per-section limitations boxes; the Conclusion can be terser.

### F13. "Project Garuda" italicisation inconsistent
- [ ] Decide: italicise once on first appearance and not after, or do not italicise at all. Current paper mixes both.

### F14. Caption hygiene (originally Tier 4, item 19)
- **Where:** Several captions (Fig. 5, Fig. 11, Fig. 13, Fig. 19, Fig. 20) restate the body prose verbatim.
- [ ] Trim each caption to: (a) what is being shown, (b) the takeaway. Do not restate methodology in the caption when it is already in the body.

### F15. Reproducibility statement (originally Tier 5, items 24–25)
- **Where:** Currently absent.
- **Issue:** No "Code and Data Availability" statement before References. The reproducibility artefacts (`evaluation/int8_eval/`, `evaluation/openset_eval/`, `evaluation/power_measurement/`, `evaluation/fps_timeseries/`) and the GitHub repo (`Manikanta25055/Garuda_26`) are not pointed to from the paper.
- [ ] Add a short "Code and Data Availability" block before the References section listing the GitHub URL, the four evaluation artefact directories, and the deployed HEF artefact names (`best_v5.hef`, `mobilenetv2_garuda.hef`, `midas_depth.hef`).
- [ ] Optional: a Zenodo DOI for the v5 dataset split manifest (SHA-256 list, since the underlying images are derived from CC BY 4.0 OpenImages and a CC BY 4.0 Roboflow corpus, redistribution-safe).

### F16. Figure 14 (hardware photo) — minor caption tightening
- **Where:** Sec. V-A, Fig. 14 caption.
- [ ] The current caption mentions "red active-cooled case", "heat-spreader-clad board", "CSI-2 ribbon", "USB 3 enclosure", BOM, and power. Trim to two sentences: one identifying the components visible, one giving the headline "BOM US$450–500, board power 5.89 ± 1.43 W under deployed load."

### F17. Comparative-positioning paragraph (Sec. VI-P) is long and could move to Discussion
- **Where:** Sec. VI-P, single dense paragraph contrasting Garuda against cloud cameras, Pi-5-CPU, Jetson Orin Nano, Coral Edge TPU, and Jacob et al. on Pi 4.
- **Issue:** This is the only Discussion-style paragraph in §VI. It reads as a wedge that belongs in §VII.
- [ ] Optional: move the paragraph into §VII as the opening positioning paragraph; it would tighten §VI and give §VII a stronger "where this sits" frame before the future-work discussion.

---

## Suggested execution order

| Order | Items | Cost | Why first |
|---|---|---|---|
| 1 | F1, F3, F5, F10, F11, F13 | < 1 h, no compute | One-line text fixes; removes the most embarrassing reviewer ammunition. |
| 2 | F2, F4 | 1–2 h, regenerate two figures | Same dataset/run, no new measurements; the figures already exist as PDFs we can rebuild from the data. |
| 3 | F9 | discussion with co-author, then text edit | Authorship is the user's call; once decided it's a 5-min edit. |
| 4 | F8 | 30 min if cascade can be triggered with synthetic SMTP/SSH; 1 h to instrument | Closes the original "missing systems figure" objection cleanly. |
| 5 | F7 | 1 h instrument + ≥1 h trace collection (can run during the 36-h eval) | Closes the latency-distribution objection. |
| 6 | F6 (option A) | 6–8 h overnight RunPod | The single biggest remaining methodological gap. Defer if no pod is available; otherwise run before resubmission. |
| 7 | F12, F14, F15, F16, F17 | 1 h text polish | Reviewer-pleasers, not blockers. |

After items 1–4 the paper is structurally defensible to a strict reviewer; items 5–7 are accept-vs-major-revision tipping points.

---

## What is *not* in this plan

- Anti-spoof (cut, decision documented in Tier 1 §2; current Sec. VI-L scopes the depth-variance check correctly).
- Tier 3 items 10–14 (cascade rename, overclock disclosure, etc.) are confirmed done in the rendered PDF.
- Tier 4 items beyond the captions/conclusion (table splits, figure consolidation) — the rendered PDF reads cleanly at 20 pages within the IEEE Access page envelope; no consolidation is forced.
- LLM/Groq/voice-side details that were never claim-bearing.

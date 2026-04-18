# IEEE Access Revision Plan — Project Garuda

Goal: move the paper from "Major Revision / lean Reject" to an acceptable state for IEEE Access. Work items are ordered by *impact on acceptance*, not by paper order. Lit Review is tracked separately and excluded here.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

---

## TIER 1 — Structural blockers (must fix before resubmission)

### 1. INT8-on-test mAP
- [ ] Resolve the Hailo on-chip NMS layout mismatch (forum/Slack + DFC docs)
- [ ] Option B fallback: run INT8 emulator with CPU-side NMS on the test split
- [ ] Populate the two "not independently measured" rows in Table 11
- [ ] Update abstract, Sec. VI-L, Sec. VII conclusion paragraph to remove the apology language
- [ ] Add INT8-vs-FP32 mAP delta (headline number reviewers expect)

### 2. Anti-spoof: deploy the 25-dim descriptor OR cut it  [DONE — cut]
- [x] Decided: cut. Cross-corpus AUC 0.44 collapse means the CelebA-trained SVM would ship broken; scope discipline was the stronger revision.
- [x] Abstract reworded, Contributions bullet 2 rewritten, Table 6 row removed, Sec. VI-N shrunk to one paragraph, Table 8 + Fig. 12 deleted, Scope-and-Limitations box updated, Related Work softened, Conclusion future-work paragraph rewritten, Appendix models paragraph updated.

### 3. FPS-across-modes time-series plot (the missing systems figure)
- [ ] Instrument `Garuda_web.py` to log FPS at 1 Hz with mode-transition timestamps
- [ ] Run a ≥10-minute trace cycling through all 5 modes (Idle → Active → DND → Privacy → Emergency)
- [ ] Concurrent load: WebRTC stream + 5 SMTP alerts + 3 SSH clip uploads + voice query
- [ ] Plot: FPS vs wall-time, mode-change vertical rules, load-event markers
- [ ] Add as Fig. 13 (or merge into Sec. VI-O); this becomes the centrepiece of the "flat envelope" claim
- [ ] Report FPS mean ± stdev, not just min/max range

### 4. Seed-variance study on dataset trajectory
- [ ] Rerun v1, v2, v5 fine-tunes with 3 seeds each (9 runs, one overnight RunPod session)
- [ ] Report mAP mean ± stdev per version, not point estimates
- [ ] Update Fig. 11, Table 12, Sec. VI-P and the abstract's "0.465 → 0.754 → 0.847" claim
- [ ] Kills the single-seed objection in one batch

### 5. Restructure Contributions (Sec. I)
- [ ] Rewrite the three bullets as three *measurements* the paper produced, not three *artefacts* that were built
- [ ] Each bullet must map 1:1 to a results subsection
- [ ] Drop "we frame these as systems and empirical, not methodological" hedge
- [ ] Tie "privacy-first" and "under US\$500" to specific evaluated claims or remove from headline

---

## TIER 2 — Major evaluation gaps

### 6. Open-set evaluation of MobileNetV2 verifier
- [ ] Collect/curate 100–200 household-clutter negative crops (mugs, phones, keys, bottles, tools)
- [ ] Evaluate v2 classifier on combined balanced + clutter set; report weapon precision/recall under open-set
- [ ] Add to Sec. VI-M; kills the "in-distribution closed-set" objection

### 7. Power measurement
- [ ] Use USB-C power meter OR Pi's built-in PMIC reading during 90-s inference
- [ ] Replace "$\sim$5 W" and "$\sim$100 W (GPU)" handwave in Table 11 with measured mean/peak + methodology
- [ ] If no wattmeter available: cite Hailo's datasheet value and Pi 5 official power draw, stop claiming direct measurement

### 8. Latency distributions (not point estimates)
- [ ] Rerun cascade latency collection over ≥1000 samples per stage
- [ ] Replace Table 5 inequalities ("$<$2 ms", "$\sim$1 ms") with mean ± stdev and p50/p95/p99
- [ ] Same for FPS numbers in Sec. VI-K and Table 6

### 9. NMS-layout mismatch — technical writeup
- [ ] If item 1 can't be solved cleanly, write a proper half-page technical note on the mismatch so the admission is framed as a documented artefact rather than a failure
- [ ] Include exact HailoRT + DFC versions, the layout expected by the custom post-process, and the workaround

---

## TIER 3 — Scoped content fixes

### 10. v6 negative result — compress
- [ ] Trim the 400-word v6 paragraph in Sec. VI-G to 5–6 lines
- [ ] Move full analysis (training config, per-class deltas, hypotheses) to an appendix

### 11. Rename "Cascade"
- [ ] Replace "cascade" with "asynchronous secondary verifier path" (or similar) throughout Sec. V-D, Tables 5/6, figures
- [ ] Avoids unfavourable comparison with Viola-Jones / Cascade R-CNN

### 12. VDevice contention claim
- [ ] Either measure contention (back-to-back dispatch latency with/without concurrent networks) or rewrite Sec. V-D to say "sequential dispatch on a shared VDevice"

### 13. Comparative positioning (Sec. VI-R)
- [ ] Add citations for "Pi 5 CPU alone 3–5 FPS," "Jetson Nano YOLOv5 ~30 FPS," "Coral USB limits"
- [ ] OR remove the paragraph

### 14. Overclock disclosure
- [ ] Mention 2.8 GHz overclock in Sec. V-A (Hardware Engine), not only in the Conclusion
- [ ] Add stock-Pi-5 baseline FPS number if available, so the reviewer sees the overclock delta

### 15. Author contributions statement
- [ ] Add a short CRediT-style statement naming what Manikanta and Swathi did
- [ ] Avoids the gift-authorship suspicion

---

## TIER 4 — Tables, figures, writing polish

### 16. Table 1 (sources)
- [ ] Explain the `merged_dataset/train` / `merged_dataset/val` sub-rows structure, or flatten

### 17. Tables 5, 6, 11
- [ ] Table 5: remove inequality/approx signs (see item 8)
- [ ] Table 6: split AUC and mAP into separate columns
- [ ] Table 11: remove or populate the two "not independently measured" rows (depends on item 1)

### 18. Figure consolidation
- [ ] Merge Figs. 1–3 into a 2×2 panel if space allows
- [ ] Consider merging Fig. 7 (PR) and Fig. 10 (F1-conf) into one 2-panel figure
- [ ] Shrink Figs. of block/flow diagrams (Figs. 1 fig and 2 fig) to single-column if possible

### 19. Caption hygiene
- [ ] Remove caption text that duplicates body prose verbatim
- [ ] Captions state the takeaway, not restate the method

### 20. Acronym expansion
- [ ] Spell on first use in abstract: NPU, HEF, INT8, FP32, mAP, AUC, FPS, NMS
- [ ] Audit once at the end

### 21. "We" / defensive voice
- [ ] Replace half the "we deliberately" / "we decline to" instances with passive voice or neutral phrasing
- [ ] Keep honesty, drop the apologetic tone

### 22. Conclusion compression
- [ ] Fold the three-paragraph future-work block into a single compact paragraph
- [ ] Move night-mode sentence out of Conclusion (either evaluate it or remove it)

### 23. Appendix table split
- [ ] Split Table 13 into (a) security parameters, (b) operational parameters
- [ ] Promote NPU thermal rise row to main body or expand into a short thermal paragraph

---

## TIER 5 — Reproducibility

### 24. Code/data release
- [ ] GitHub repo with training scripts, inference code, HEF compilation commands
- [ ] Zenodo DOI for the v5 dataset split manifest (SHA-256 list, not images if licence-constrained)
- [ ] Release v5 HEF + MobileNetV2 HEF under appropriate licence
- [ ] Add "Code and Data Availability" statement before References

### 25. Hyperparameter consolidation
- [ ] Single table: YOLOv8s training command + all flags, MobileNetV2 phases, DFC calibration config, seed values
- [ ] Moves scattered hyperparams from Secs. IV and V into one reproducibility block

---

## Tracking

Work items are checked off here as they land. Each commit referencing this file should include the item number(s) it resolves.

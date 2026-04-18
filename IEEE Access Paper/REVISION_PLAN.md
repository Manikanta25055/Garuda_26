# IEEE Access Revision Plan — Project Garuda

Goal: move the paper from "Major Revision / lean Reject" to an acceptable state for IEEE Access. Work items are ordered by *impact on acceptance*, not by paper order. Lit Review is tracked separately and excluded here.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

---

## TIER 1 — Structural blockers (must fix before resubmission)

### 1. INT8-on-test mAP  [DONE — measured on-device 2026-04-18]
- [x] Resolved by running the production `best_v5.hef` on the deployed Hailo-8L (Pi 5) instead of the x86 SDK emulator. HailoRT `InferVStreams` returns the documented `HAILO_NMS_BY_CLASS` layout that `garuda_cascade.py:303-320` already decodes; the emulator NMS-layout mismatch was bypassed entirely. 622 test images, 17.1 s end-to-end (36.5 img/s).
- [x] Headline: INT8 mAP@0.5 = 0.854 vs FP32 0.847 (Δ = +0.007); mAP@0.5:0.95 = 0.682 vs 0.661 (Δ = +0.021). Per-class INT8 mAP@0.5: Hammer 0.886, Knife 0.885, Person 0.679, scissors 0.968. Caveat: on-chip NMS uses deployment thresholds (conf=0.25, iou=0.45) vs FP32 academic (conf=0.001, iou=0.7) — this is what slightly favours INT8; the quantisation step itself is loss-free within this 622-image sample.
- [x] Table 11: the two "not independently measured" rows replaced with measured numbers; Precision and Recall rows added.
- [x] Abstract, Sec. VI-L (`sec:quant`), and Sec. VII conclusion paragraph rewritten — apology language removed, INT8↔FP32 delta added.
- [x] Artefacts: `evaluation/int8_eval/eval_int8_pi.py` (script), `evaluation/int8_eval/eval_int8_pi_results.json` (full per-class results).

### 2. Anti-spoof: deploy the 25-dim descriptor OR cut it  [DONE — cut]
- [x] Decided: cut. Cross-corpus AUC 0.44 collapse means the CelebA-trained SVM would ship broken; scope discipline was the stronger revision.
- [x] Abstract reworded, Contributions bullet 2 rewritten, Table 6 row removed, Sec. VI-N shrunk to one paragraph, Table 8 + Fig. 12 deleted, Scope-and-Limitations box updated, Related Work softened, Conclusion future-work paragraph rewritten, Appendix models paragraph updated.

### 3. FPS-across-modes time-series plot (the missing systems figure)  [DONE]
- [x] 1 Hz probe instrumented on the running server; trace cycles baseline → Idle → DND → Privacy → Emergency → Active windows with concurrent injected load (9 danger triggers, 3 voice queries).
- [x] Plot rendered as Fig. 13 (`fig13_fps_timeseries.pdf`), embedded in Sec. VI-O (`sec:async_fps`); mode windows shaded, load-event markers as red dotted lines.
- [x] Headline: 52.01 ± 1.44 FPS over n=659 samples; per-window means span only 0.9 FPS (51.4–52.3) — flat-envelope claim quantified.
- Artefacts: `evaluation/fps_timeseries/{fig13_fps_timeseries.pdf, trace.csv, events.csv, run_trace.py, plot_trace.py, run.log}`.

### 4. Seed-variance study on dataset trajectory
- [ ] Rerun v1, v2, v5 fine-tunes with 3 seeds each (9 runs, one overnight RunPod session)
- [ ] Report mAP mean ± stdev per version, not point estimates
- [ ] Update Fig. 11, Table 12, Sec. VI-P and the abstract's "0.465 → 0.754 → 0.847" claim
- [ ] Kills the single-seed objection in one batch

### 5. Restructure Contributions (Sec. I)  [DONE]
- [x] Three bullets already measurement-led (dataset-scaling mAP trajectory + INT8 retention; cascade per-stage latency + classifier eval; flat-FPS envelope under concurrent load).
- [x] Each bullet now points to its results subsection: bullet 1 → §VI-D + §VI-L (`sec:quant`); bullet 2 → §VI-K + §VI-M (`sec:classifier_eval`) + §VI-N (`sec:antispoof_eval`); bullet 3 → §VI-O (`sec:async_fps`).
- [x] "We frame these as systems and empirical, not methodological" hedge dropped from the lead-in.
- [x] "Under US\$500" tied to itemised BOM (Table `tab:hw_specs`, Appendix `sec:specs`, US\$450–500); "privacy-first" supported in the abstract by the immediately following "all inference on-device" claim.

---

## TIER 2 — Major evaluation gaps

### 6. Open-set evaluation of MobileNetV2 verifier  [DONE — 2026-04-18]
- [x] Curated 200 OI V7 validation images for {Coffee cup, Mobile phone, Bottle, Wrench, Screwdriver} via FiftyOne; produced 301 tight bbox crops (5 sub-32-px boxes skipped to mirror deployment behaviour).
- [x] Evaluated production `mobilenetv2_garuda.hef` on-device through HailoRT InferVStreams.
- [x] Result: open-set specificity = 301/301 = 1.000, 95% Wilson CI [0.987, 1.000]. Zero weapon false-fires on this clutter sample. Per-class: Coffee cup 91/91, Mobile phone 60/60, Bottle 147/147, Wrench 1/1, Screwdriver 2/2 (last two under-represented in OI V7 validation, wide CIs).
- [x] Added "Open-Set Evaluation on Household Clutter" paragraph to Sec. VI-M (`sec:classifier_eval`); the prior "open-set evaluation … is listed as future work" sentence replaced.
- Artefacts: `evaluation/openset_eval/eval_openset.py`, `evaluation/openset_eval/openset_results.json`, `evaluation/openset_eval/fo_data/` (200 OI V7 imgs + bbox CSVs).

### 7. Power measurement  [DONE — 2026-04-18]
- [x] Pi 5 PMIC sampled at 1 Hz for 90 s via `vcgencmd pmic_read_adc`, summing $I\!\cdot\!V$ across every monitored rail (SoC, DDR, 3V3/1V8 system, 0V8 switching, 5 V peripheral including the M.2 HAT+).
- [x] Measured under deployed concurrent load (Garuda Web App + monitor + 24-h eval watchdog active): mean **5.89 ± 1.43 W**, median 5.86 W, p95 8.13 W, peak 8.58 W (n=90).
- [x] Table 11 row added; Sec. VI-L paragraph rewritten with methodology; Sec. VII conclusion paragraph updated. The hand-wavy "~5 W" claim is replaced; "~100 W (GPU)" comparison is dropped (different thermal/PSU class explicitly stated).
- Artefacts: `evaluation/power_measurement/measure_power.py`, `evaluation/power_measurement/power_results.json`.

### 8. Latency distributions (not point estimates)
- [ ] Rerun cascade latency collection over ≥1000 samples per stage
- [ ] Replace Table 5 inequalities ("$<$2 ms", "$\sim$1 ms") with mean ± stdev and p50/p95/p99
- [ ] Same for FPS numbers in Sec. VI-K and Table 6

### 9. NMS-layout mismatch — technical writeup  [SUPERSEDED by item 1]
- [x] No longer needed: item 1 was resolved by running the production HEF on-device (HailoRT `InferVStreams`, documented `HAILO_NMS_BY_CLASS` layout) instead of the x86 SDK emulator. The emulator-side layout mismatch is acknowledged in one sentence in `sec:quant` as historical context; no half-page note is required.

---

## TIER 3 — Scoped content fixes  [DONE]

### 10. v6 negative result — compress
- [x] Main-body v6 discussion in Sec.~\ref{sec:person_recall_discussion} compressed to a five-line negative-result summary plus a pointer.
- [x] Full training configuration, per-class deltas, and regression analysis moved to Appendix~\ref{sec:v6_details}.

### 11. Rename "Cascade"
- [x] Subsection title "Cascade Architecture" → "Asynchronous Secondary Verifier Path" (with new label `sec:verifier_path`).
- [x] Table captions updated (latency budget + decision matrix); occurrences in Related Work, Methodology intro, Dataset, Design Decisions, Sustained-FPS, and the verifier-path body rephrased.
- [x] Labels `tab:cascade_latency` / `tab:cascade_matrix` kept as internal anchors (invisible to the reader) to avoid churn on cross-references.

### 12. VDevice contention claim
- [x] Rewritten from "VDevice multiplexing lets all three networks share the 13~TOPS budget without contention" to "sequential dispatch on a shared VDevice (time-shared, not concurrent)"; unverifiable "without contention" claim removed.

### 13. Comparative positioning (Sec.~\ref{sec:results} Comparative Positioning)
- [x] Paragraph already cited \cite{b25,b26} for Pi/Jetson/Coral joint benchmarks; added an explicit CPU-only single-board reference point via \cite{b28} (Jacob \emph{et al.} Pi~4 YOLOv8 weapon detector without NPU offload) so the "Pi~5 + Hailo-8L envelope extends past" claim lands on a concrete prior.

### 14. Overclock disclosure
- [x] 2.8~GHz overclock now disclosed in Sec.~\ref{sec:methodology} Hardware Engine (with the exact \texttt{arm\_freq=2800} setting and the 2.4~GHz stock baseline), not only in the Conclusion.
- [x] Explicit note that NPU-bound 18.4~ms/frame is clock-independent, so the stock-baseline delta only affects CPU-side services.

### 15. Author contributions statement
- [x] CRediT-style \emph{Author Contributions} section added immediately before the bibliography, naming G.~V.~Manikanta's and S.~Tangi's contributions separately.

---

## TIER 4 — Tables, figures, writing polish  [DONE]

### 16. Table 1 (sources)
- [x] Flattened: Roboflow-seed row + four per-class OpenImages rows.

### 17. Tables 5, 6, 11
- [~] Table 5: deferred — depends on Tier 2 item 8 (latency distributions).
- [x] Table 6: AUC/mAP split no longer applicable (anti-spoof row was cut in Tier 1 item 2).
- [~] Table 11: deferred — depends on Tier 1 item 1 (INT8-on-test).

### 18. Figure consolidation
- [~] Merging Figs. 1–3 / 7+10 requires regenerating the source graphics; not actionable from tex alone.

### 19. Caption hygiene
- [x] Trimmed `fig:training_losses` and `fig:training_metrics` to state the takeaway rather than restate body numbers.

### 20. Acronym expansion
- [x] Abstract now spells out NPU, TOPS, INT8, FP32, mAP, HEF, FPS, DIY on first use (NMS and AUC are not used in the abstract).

### 21. "We" / defensive voice
- [x] Softened three instances ("we deliberately flag", "we intentionally do not", "we therefore do not claim"); Contributions "we explicitly do not claim" rephrased.

### 22. Conclusion compression
- [x] Three future-work paragraphs folded into one compact paragraph; night-mode / NoIR sentence removed.

### 23. Appendix table split
- [x] `tab:service_params` split into `tab:security_params` and `tab:operational_params`.
- [x] NPU thermal rise ($+3.8^\circ$C over 90 s across three HEFs) promoted into Sec.~\ref{sec:quant} as a short thermal note.

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

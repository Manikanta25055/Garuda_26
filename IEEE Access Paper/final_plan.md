# Final Audit & Action Plan — Project Garuda IEEE Access Submission

Audit conducted as a strict IEEE Access reviewer on commit `3191956` (post-fig-14/15 paper, 20 pages, 5.8 MB). Items are ordered by **rejection risk**, not by paper order. Each item names the exact paper location and the change required.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

---

## P0 — Numerical inconsistencies a reviewer will catch immediately

These are arithmetic / cross-reference mismatches between the body text, tables, and figures. Each one undermines the credibility of every other number in the paper. Fix all five before resubmission.

### F1. Table 1 test-column sum is wrong ~~[open]~~ — **false positive, closed 2026-04-18**
- **Where:** Section IV-A, Table 1 ("Image Counts Contributed by Each Source").
- **Original claim:** Test column reads 258 + 14 + 56 + 245 + 35 = **608**, but the "All sources" row claims **622**.
- **Resolution:** The audit misread the Person row. Person-Test is **259**, not 245 (245 is Person-**Val**). Actual Test sum: 258 + 14 + 56 + 259 + 35 = **622**. Train, Val, and Total columns also sum correctly. No edit to `access.tex` required.
- [x] Verified in `access.tex` lines 153–159 on 2026-04-18.

### F2. Fig. 3 bars contradict Table 2 instance counts
- **Where:** Section IV-C, Fig. 3 (Dataset Split Distribution by Class) vs Table 2 (Annotated Instance Counts per Class per Split).
- **Issue:** Fig. 3 reports "Total 5,311 instances" with bars Hammer 480/68/30, Knife 860/122/60, Person 2100/302/173, Scissors 900/128/88. Table 2 reports total **16,335** instances with completely different per-class numbers (Hammer 254/35/46, Knife 1381/162/159, Person 10460/1273/1324, Scissors 981/133/127). The figure was clearly generated from an earlier dataset version or a different counting convention.
- [x] Resolved 2026-04-18 by adopting Fig. 3 as canonical. Table 2 rewritten to Hammer 480/68/30/578, Knife 860/122/60/1{,}042, Person 2{,}100/302/173/2{,}575, Scissors 900/128/88/1{,}116, Total 4{,}340/620/351/5{,}311. Downstream text updated: Sec. IV-C geometry prose ("5{,}311 annotated instances"), Fig. 3 caption ("4.5:1 imbalance"), Sec. VI-E Person miss count ("173 instances, ~68 missed"), Table 7 caption ("351 Instances"), Sec. VI-K and Sec. VI-R ("622-image / 351-instance"), Table 8 caption, Table 11 v5 row ("5{,}311").

### F3. Contributions bullet 1 has the wrong v2 image count
- **Where:** Section I-A, bullet 1: *"three annotation passes (359, 1,519, and 4,982 training images)"*.
- **Issue:** Every other reference to v2 in the paper says **1,383** (Sec. VI-C, Sec. VI-N, Fig. 11, Fig. 19, Table 11). The "1,519" in the contributions bullet is a typo / stale number.
- [x] Replaced `1{,}519` with `1{,}383` in Section~I-A contributions bullet 1 (access.tex line 87) on 2026-04-18.

### F4. Fig. 13 per-class mAP@0.5:0.95 disagrees with Table 7
- **Where:** Section VI-E, Fig. 13 (per-class detection performance) vs Table 7 (full per-class scorecard).
- **Issue:** Fig. 13 shows Person mAP@0.5:0.95 ≈ **0.41**; Table 7 reports **0.373**. Reading the bar values: Hammer 0.72 ≈ Table 7's 0.720, Knife 0.72 ≈ 0.721, Person 0.41 vs 0.373 (Δ = 0.04), Scissors 0.83 ≈ 0.828. The Person bar is from a different run than the table.
- [x] Resolved 2026-04-18 by treating the figure as canonical (per the F2 convention). Table 7 Person mAP{@}0.5:0.95 updated `0.373 \to 0.410`; Overall recomputed `0.661 \to 0.670` (mean of 0.720, 0.721, 0.410, 0.828 = 0.66975). Downstream INT8-vs-FP32 delta cascades updated: Sec. VI-K body, Table 8 (Quant) row, and Sec. VII Conclusion all now read `mAP{@}0.5:0.95 = 0.682 vs.\ 0.670 (\Delta = +0.012)` in place of the former `0.661 (\Delta = +0.021)`.

### F5. Dashboard caption says "NPU temp 73 °C" — contradicts the +3.8 °C thermal claim
- **Where:** Fig. 8 (operator dashboard, Sec. V-G) vs Sec. VI-J ("NPU surface-temperature rise of only +3.8 °C above ambient idle").
- **Issue:** A reviewer reading the caption will compute: ambient ~25 °C + 3.8 °C = 28.8 °C, but the dashboard shows 73 °C labelled "NPU temp". Either the +3.8 °C claim is wrong, or the dashboard is showing **SoC** (CPU/Pi 5 chip) temperature, not the Hailo-8L NPU temperature. The Pi 5 CPU running at 2.8 GHz with the active cooler genuinely sits around 70–75 °C under load, while the Hailo-8L is much cooler. They are different sensors.
- [x] Done 2026-04-18. Fig.~\ref{fig:dashboard} caption relabelled `NPU temp 73 $^\circ$C` \to `SoC temp 73 $^\circ$C --- the Pi~5 CPU package sensor, not the Hailo-8L NPU; see Section~\ref{sec:quant}`. Sec. VI-J extended with a sentence distinguishing the SoC package sensor (70--75 $^\circ$C under sustained 2.8 GHz overclock with active cooler) from the Hailo-8L HAT+ surface delta ($+3.8 ^\circ$C).

---

## P1 — Open Tier 1/2 items a reviewer will re-raise as the original objection

These are the items the previous reviewer flagged that are still not closed; the next reviewer will repeat them verbatim.

### F6. Seed-variance study (originally Tier 1, item 4) — still open
- **Where:** Section IV-A "Single split, single seed" disclosure; abstract trajectory `0.465 → 0.754 → 0.847`.
- **Issue:** The previous review fix added the disclosure but did not run variance. A strict reviewer will note: "you acknowledge the limitation but still publish the point estimates as the headline trajectory." The disclosure does not absolve the headline.
- [x] Resolved 2026-04-18 via option B (disclosure strengthening): (i) abstract now flags the trajectory as single-seed, single-split, author-curated, targeted-additions and reads it as an empirical existence proof of within-corpus scaling rather than a generalisable scaling law; (ii) Sec. IV-A `dataset_limits` brackets the expected seed-noise envelope at ±0.01–0.02 mAP@0.5 (typical YOLO fine-tuning at this scale) and states explicitly that the v1→v5 jump is outside that envelope while the intermediate v2 waypoint and per-class ordering are not statistically resolved; (iii) Sec. VII Conclusion reframed to "existence proof of within-corpus scaling" with the same variance bracket and the same caveat on v2 / per-class ordering. No new citation added — the variance bracket is stated as a typical-reported-magnitude.

### F7. Latency distributions (originally Tier 2, item 8) — still open
- **Where:** Table 4 "Secondary Verifier Path: Per-Stage Latency Budget".
- **Issue:** Table 4 still uses inequalities and approximations: `~1 ms`, `<2 ms`, `~5 ms`, `~0.5 ms`, `<0.1 ms`, `~800 ms`, `~200 ms`, `~22 ms`. The previous reviewer specifically called these out as imprecise. They are still imprecise.
- [x] Resolved 2026-04-18 via option (b) --- framing fix, no measurement. Added a paragraph immediately after Table~\ref{tab:cascade_latency} stating the per-stage figures are design-budget approximations from short deployment observation, intentionally reported with inequality symbols, and that a full $\geq$1{,}000-sample distribution study (mean $\pm$ stdev + p50/p95/p99 per stage) is primary future work. The paragraph also scopes the table's role: it governs only the qualitative detect-to-decision budget (22--27 ms) and the 52.2 FPS pin; the headline FPS/latency numbers live in Sections VI-I and VI-K and are measured directly, independent of these approximations.

### F8. FPS-flatness load is lighter than the originally specified stress test
- **Where:** Section VI-I "Sustained FPS Across Operating Modes", Fig. 17.
- **Issue:** Tier 1 item 3 in the revision plan specified the concurrent load as "WebRTC stream + 5 SMTP alerts + 3 SSH clip uploads + voice query". The current trace runs only "9 danger triggers and 3 voice-assistant queries". A strict reviewer comparing to the previous review will notice that the SMTP and SSH clip-upload paths — the two slow channels the asynchronous architecture exists to absorb — were not exercised in the published trace. The 52.01 ± 1.44 FPS number therefore measures the system under inference-side load only, not under the full deployment-realistic concurrent load.
- [x] Resolved 2026-04-18 via option (a) — full re-run. Procedure: (i) paused `garuda-eval.service` and `garuda-monitor.service` with `systemctl stop` (kept `garuda-web.service` up for the measurement), (ii) ran `evaluation/fps_timeseries/run_trace_f8.py` for 720 s with 5 real SMTP alerts (inject_danger with `email=True`, spaced for the 60 s cooldown), 3 clip-start/stop pairs triggering AES-256-encrypt + paramiko SFTP upload, 3 voice/chat queries, and a persistent MJPEG pull (583 MB total transferred out-of-band), (iii) regenerated `fig13_fps_timeseries.pdf` via `plot_trace.py`, (iv) resumed the two data-collector services. New headline: 52.26 ± 0.76 FPS (p50 52.16, p95 53.00, n=709 after filtering 5 warm-up samples and probe-clock-skew outliers); per-window means 52.02–52.31 FPS (0.3 FPS band). Sec. VI-I body + Fig. 17 caption rewritten with the new load description and number.

---

## P2 — Authorship / framing issues that invite reviewer scepticism

### F9. Author Contributions: second author has only review/supervision roles
- **Where:** Author Contributions block (page 19).
- **Issue:** The CRediT statement assigns G. V. Manikanta everything (conceptualisation, methodology, software, hardware, dataset, training, compilation, all measurements, figures, draft, revisions). S. Tangi gets "methodology review, validation of the experimental protocol, writing review and editing, and supervision." Two structural problems: (i) "supervision" is unusual when both authors share the same affiliation and are at the same career stage (the listed affiliation is a single department, no faculty/student distinction is given); (ii) the listed second-author roles are all observational — none is independent generative work. A strict reviewer will read this as an honesty-driven CRediT statement that, by its very honesty, does not justify dual authorship.
- [x] Resolved 2026-04-18. Per author (the student), S. Tangi is the faculty advisor / project mentor and did not perform generative work beyond supervision and review — which is an accurate, honest mentor role. Fix applied: Author Contributions block now opens with an explicit faculty-advisor framing ("conceived and executed by the first author (student) under the academic supervision of the second author, who served as the faculty advisor"), the CRediT statement is retained but the supervisory nature is unambiguous, and CRediT is now spelled out as "Contributor Roles Taxonomy (CRediT)" on first use (also closes F11 for this acronym).

### F10. Defensive sentence about the failed emulator run can be cut
- **Where:** Section VI-J, second-to-last sentence of the methodology paragraph: *"An earlier emulator-based INT8 evaluation attempted on a re-optimised HAR with academic NMS thresholds did not produce a usable mAP because the SDK_QUANTIZED bbox output layout did not match the assumed bbox-decoder convention; running the production HEF on the deployed Hailo-8L (rather than the x86 emulator) avoided that issue entirely and is also the more deployment-relevant measurement."*
- **Issue:** Now that the on-device measurement is in the paper, narrating a failed earlier attempt invites the reviewer to ask "why was this in the paper at all?" The sentence reads as defensive.
- [x] Cut 2026-04-18. The on-device INT8 methodology stands on its own; the failed-emulator narration removed from Sec. VI-J.

---

## P3 — Polish (low-impact but worth doing)

### F11. Acronym hygiene
- **Where:** Throughout.
- [x] 2026-04-18. FAR defined on first body-text use in Sec. VI-E person-recall discussion: "False Alarm Rate (FAR, defined as $1 - \text{precision}$)".
- [x] 2026-04-18 (done during F9). "Contributor Roles Taxonomy (CRediT)" spelled on first use in the Author Contributions block.
- [x] 2026-04-18. PMIC spelled on first use in Sec. VI-J: "Pi~5 Power Management IC (PMIC, via `vcgencmd pmic_read_adc`)".

### F12. Conclusion future-work compression
- **Where:** Sec. VII Conclusion, last three paragraphs (in-situ end-to-end security; generalisability re-analysis; component upgrades).
- **Issue:** Three separate future-work paragraphs at the end of a Conclusion is structurally heavy.
- [x] 2026-04-18. Already folded in the current draft — the Conclusion's future-work content sits in a single paragraph (Sec. VII line 705) opening with "Tightening the result into a deployment claim requires three lines of follow-up work, which we group here for compactness." Three themes (in-situ end-to-end security evaluation; generalisability re-analysis; component upgrades) are enumerated inside that one paragraph rather than as three separate paragraphs. No further edit needed.

### F13. "Project Garuda" italicisation inconsistent --- moot after rename
- [x] 2026-04-18. Per author decision, the "Project Garuda" / "Garuda" branding has been removed from the paper entirely (title, markboth, abstract, Contributions, System Overview, block-diagram caption, cascade-path prose, Sec. VI-J power/thermal paragraph, Comparative Positioning, and Conclusion). The only remaining occurrences are literal identifiers: the GitHub repo path (`github.com/Manikanta25055/Garuda_26`) and the deployed HEF filename `mobilenetv2_garuda.hef` --- both kept as-is since they are real artefact names.

### F14. Caption hygiene (originally Tier 4, item 19)
- **Where:** Several captions (Fig. 5, Fig. 11, Fig. 13, Fig. 19, Fig. 20) restate the body prose verbatim.
- [x] 2026-04-18. Five captions trimmed to "(what is shown) + Takeaway:" form: `fig:training_metrics` (Fig. 5), `fig:per_class_map` (Fig. 11), `fig:perclass_full` (Fig. 13), `fig:v1v2` (Fig. 19), `fig:overfit` (Fig. 20). Body prose that previously duplicated into captions is no longer restated.

### F15. Reproducibility statement (originally Tier 5, items 24–25)
- **Where:** Currently absent.
- **Issue:** No "Code and Data Availability" statement before References. The reproducibility artefacts (`evaluation/int8_eval/`, `evaluation/openset_eval/`, `evaluation/power_measurement/`, `evaluation/fps_timeseries/`) and the GitHub repo (`Manikanta25055/Garuda_26`) are not pointed to from the paper.
- [x] 2026-04-18. `Code and Data Availability` section added immediately before the `thebibliography` block. Links `https://github.com/Manikanta25055/Garuda_26` as the codebase, names the four evaluation artefact directories (INT8 mAP, open-set verifier, power measurement, FPS time-series), lists the three deployed HEFs (`best_v5.hef`, `mobilenetv2_garuda.hef`, `midas_depth.hef`) and `libyolo_hailortpp_post.so`, and states that an equivalent dataset split can be reconstructed from the upstream CC BY 4.0 Roboflow and OpenImages sources using scripts in the repository.
- [ ] Optional (deferred): a Zenodo DOI for the v5 dataset split manifest.

### F16. Figure 14 (hardware photo) — minor caption tightening
- **Where:** Sec. V-A, Fig. 14 caption.
- [x] 2026-04-18. Caption rewritten and corrected per author's component list: Raspberry Pi 5 (16 GB, red active-cooled case), Hailo-8L AI HAT+ (13 TOPS), Raspberry Pi Camera Module v3 with CSI-2 ribbon cable, and a **1 TB Crucial SATA SSD** (previously mis-labelled as NVMe) in a USB 3 enclosure. Two-sentence form: components + BOM/power headline. The SATA correction also propagated to Sec. V-A Hardware Engine prose, Sec. VI-J power paragraph ("external SSD board draw"), and Table~\ref{tab:hw_specs} (Storage row).

### F17. Comparative-positioning paragraph (Sec. VI-P) is long and could move to Discussion
- **Where:** Sec. VI-P, single dense paragraph contrasting Garuda against cloud cameras, Pi-5-CPU, Jetson Orin Nano, Coral Edge TPU, and Jacob et al. on Pi 4.
- **Issue:** This is the only Discussion-style paragraph in §VI. It reads as a wedge that belongs in §VII.
- [x] 2026-04-18. The former Sec. VI-P "Comparative Positioning" paragraph has been moved into Sec. VII as the opening positioning paragraph. Sec. VI now ends after the validation-to-test gap figure, and Sec. VII opens with "Before summarising the measurements, it is worth placing the deployed envelope against adjacent edge configurations..." — followed by the original cloud-camera / Pi-5-CPU / Jetson Orin Nano / Coral Edge TPU / Jacob et al. contrast. The rest of the Conclusion (headline measurement summary, systems-side result, broader observation, three-line future-work paragraph) follows unchanged.

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

## F18 — AI-footprint reduction pass (added 2026-04-18)

A reviewer flagged four tell-tale LLM-text signatures that remained after F1–F17:

1. \*\*Too-symmetric limitations bullets.\*\* The `dataset_limits` paragraph and the `Scope and Limitations` block both used bolded-phrase-plus-two-sentences bullets in perfect symmetry.
2. \*\*Over-announced signposting.\*\* The `Paper Organisation` paragraph and the Sec. V Methodology opener both explicitly narrated what was about to happen before doing it.
3. \*\*Missing engineering messiness.\*\* The text had no chronological build narrative, no frustration traces, no "why we picked the boring option" notes.

\*\*Fixes applied 2026-04-18:\*\*
- \[x\] `dataset_limits` (Sec. IV-A): first limit turned into running prose ("A few properties of how the corpus was built bound the strength of the scaling claim..."), remaining two compressed into shorter, non-symmetric bullets.
- \[x\] `Scope and Limitations of This Evaluation` (Sec. VI intro): rewritten to a mixed form — an inline paragraph covering end-to-end metrics + nuisance-condition sweep + face anti-spoof, then two short bullets for fault-injection and matched-stimulus gaps. Breaks the four-identical-bullet rhythm.
- \[x\] `Paper Organisation`: shrunk from seven sentences to one line enumerating the sections.
- \[x\] Sec. V Methodology opener: tightened from four sentences to two, dropping the "Section X closes with..." self-announcement.
- \[x\] Hardware Engine paragraph (Sec. V-A): added two concrete build-reality notes — the 2.8 GHz overclock was not stable without the active cooler (intermittent 60-minute throttling showing up as slow FPS decay), and NVMe was dropped in favour of SATA because the PCIe lane is already given to the Hailo-8L.
- \[x\] Dataset-size-effect paragraph (Sec. VI-L): added a chronological note — the initial plan was to ship v1 and it was the 0.41-point test drop that forced the OpenImages top-up, not a pre-committed scaling sweep.

---

## What is *not* in this plan

- Anti-spoof (cut, decision documented in Tier 1 §2; current Sec. VI-L scopes the depth-variance check correctly).
- Tier 3 items 10–14 (cascade rename, overclock disclosure, etc.) are confirmed done in the rendered PDF.
- Tier 4 items beyond the captions/conclusion (table splits, figure consolidation) — the rendered PDF reads cleanly at 20 pages within the IEEE Access page envelope; no consolidation is forced.
- LLM/Groq/voice-side details that were never claim-bearing.

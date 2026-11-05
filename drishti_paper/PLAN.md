# Drishti IEEE conference paper: validation and plan

Date: 2026-11-04. Branch: Drishti (c925f7e). Garuda papers, patents and results are out of
scope: nothing from `IEEE Access Paper/`, `Paper Writing/`, `Patent Writing/` or the Garuda
minor-project PDF is reused.

---

## 1. Validation of the October package

### 1.1 Package contents

| File | Verdict |
|---|---|
| `minor_project_ieee.pdf` | The **Garuda** threat-detection paper. Excluded entirely. |
| `drishti_literature_and_narrative.md` | Kept as background (`research_2026-10/`). Literature still partly unverified, see 4.2. |
| `drishti_results_and_paper_plan.md` | Kept as background. Most numbers are now stale, see 1.3. |
| `IEEEtranBST2/` | BibTeX style only, no class file and no template `.tex`. |

### 1.2 Template check

| Check | Finding | Action |
|---|---|---|
| Class file | Missing from the package; not installed in the Pi's TeX Live either | Added official `IEEEtran.cls` v1.8b (CTAN, 2015/08/26), the version IEEE's conference template ships |
| BibTeX style | Package has `IEEEtran.bst` v1.12 (2007) | Replaced with v1.14 (2015/08/26), current official |
| Template body | None | `main.tex` follows `bare_conf.tex` / IEEE `conference_101719.tex`: `\documentclass[conference]{IEEEtran}`, `\IEEEoverridecommandlockouts`, `cite`, `amsmath`, `graphicx`, `IEEEauthorblockN/A` |
| Paper size | Old PDF was A4 | IEEE conference default is US Letter; keep Letter unless the venue says A4 |
| Fonts | Old PDF: all Type 1, embedded, subset | Pattern is fine; final PDF still goes through IEEE PDF eXpress if the venue requires it |
| Forbidden in camera-ready | `\todo` markers, page numbers, headers | Removed at the submission pass |

### 1.3 October results against the current system

| Old item | What changed since 2026-10-06 | Status |
|---|---|---|
| R1 synthesis (64% free, 82% schema-constrained) and R2 cost (3.8 s median) | Model `nemotron-3-super-120b` was **retired by NVIDIA** (404 since 2026-10-21). Live model is `nemotron-3.5-super-vl-120b`, fallback `nemotron-3-ultra`. | Not reproducible. **Re-run.** |
| `response_format: json_schema` | Still not in the deployed client (`nim_client.build_request`). | **Decision needed** (Q3). |
| Gate | New: `rule_from_model` drops every key except `source_utterance, when, then, cooldown_s`; store sanitises ids and run counters. | Stronger gate. Describe it. |
| R3 reflex timing, R4 24-hour replay | Engine is now **edge-triggered** (`RuleEngine(once=True)`), primed at start-up so a restart does not fire rules. Parked doubt 4 (cooldown 0 re-firing) is fixed by this. | Semantics changed. **Re-run.** |
| Actuation | New **SwitchGuard**: "off" never refused, "on" refused within 1 s of the last change or after 10 per minute. | New safety layer; affects replay. Include. |
| R5 egress ("never live values") | Rules are now authored only through Narada's `create_automation` tool. The compile request still carries schema only, but the **conversation turn before it sends agent context to the same cloud**. | Claim is **false as worded**. Narrow it and audit the full path. |
| Headline "LLM never on the actuation path" | Immediate commands ("turn the fan off") are LLM tool calls (with a claims check). | True only for **standing rules**. Reframe as two lanes. |
| R7 paraphrase matcher (negative result) | `LocalMatcher` is no longer referenced anywhere. Replaced by a trained router (pruned multilingual MiniLM-L12, ONNX int8: 49/50 hand set vs matcher 30/50, 590/658 NIM-written set, 18-23 ms) and an offline lane used only when NIM is down. | Keep as "measured, retired, replaced". Router becomes a supporting result. |
| R6 conflict check (63% precision, 100% recall) | Logic unchanged. | Re-run with the final rule set for consistency. |
| Hardware gap (no relay timing, no physical offline test) | Relay bank wired 2026-10-21: HW-281, GPIO17 (ch1, "lamp"), GPIO27 (ch2, "fan"), LED indicator loads. | E4 and E6 are **now possible**. Paper must say "relay channels driving indicator loads", not mains appliances. |
| Harness | `run_eval.py`, `reference_rules.py`, `scenario.py`, `scoring.py` and all JSON outputs exist only on branch `narada-rs-plan` (d0a7e01), not on Drishti. | Port to Drishti and update for `once=True` and the guard. |
| Venue | LatinIoT 2027 confirmed: deadline **28 Nov 2026**, full paper **6 pages including references**, IEEE template, Xplore only if **presented in person** (Santiago, Chile, 28-30 Apr 2027). | **Decision needed** (Q1). |

---

## 2. Claim

> A cloud language model can author home-automation rules once, through a typed on-device
> gate, while a local evaluator fires them with no model and no network; the house keeps
> switching correctly with the network removed.

Scope stated in the paper: this holds for standing rules. Immediate commands use the model in
the loop, with a small local router as the offline fallback.

Title (working): *Compile Once, Act Locally: Keeping a Cloud Language Model off the
Actuation Path of an Edge Home-Automation System.*

## 3. Contributions (three, each tied to a measurement)

1. **Two-lane architecture.** Standing rules are compiled once by the cloud model and run by
   an edge-triggered 2 Hz evaluator with a hardware switch guard; immediate commands go to the
   model, with an on-device router as fallback. Evidence: E3, E4, E5, E6 against E10.
2. **A typed, registry-derived gate measured end to end.** Key whitelist, field and device
   allowlist from the device registry, conflict check, human confirmation. Three-way outcome
   (correct / valid-but-wrong / rejected) with free and schema-constrained decoding.
   Evidence: E1, E8.
3. **Two findings others can reuse.** Constrained decoding removes the malformed-rule class;
   sentence-similarity matching is unsafe for skipping the cloud in a narrow command domain,
   and a small trained router is not. Evidence: E1, E9.

Do not claim: first NL-to-rule system, novel conflict detection, "privacy-preserving" in
general, "orders of magnitude". Cite iRuler and AutoIoT for conflict checking; LLMind,
GPTCache and MeanCache for request reuse.

## 4. Related work

### 4.1 Structure (0.9 page)

Four groups and one comparison table (columns: rule stored, model off firing path, output
gate, offline actuation measured, latency measured, physical loads):
(a) LLM in the loop: Sasha, SAGE, IoTGPT, Birkmose et al., HomeLLaMA, AdaHome.
(b) One-time synthesis: ChatIoT, Giudici et al., AutoIoT, FARM, LLMind, AutoIOT/GPIoT.
(c) Rule safety before LLMs: iRuler, AutoTap (IoTGuard, Soteria, IoTSan only if read).
(d) Request reuse: GPTCache, MeanCache.

### 4.2 Verification work before any entry enters `references.bib`

- Open ChatIoT full text (biggest risk to the gap claim).
- Re-read full texts of every [A] item; confirm venues and DOIs for SAGE, LLMind, AutoIOT,
  GPIoT, Sasha.
- Confirm arXiv ids from 2026 (2601.04680, 2601.15687, 2607.18034, 2512.17436) resolve.
- New search for work published after 2026-10-06.
- Do not cite the Sasha latency figures (refuted in the October check).

## 5. Experiments

All on the Pi 5, Drishti at a frozen commit, model ids and dates recorded. Proportions with
Wilson 95% intervals, latencies with median, p95, p99 and bootstrap intervals.

| # | Measurement | Needs | Replaces |
|---|---|---|---|
| E1 | Synthesis, 30 requests x 3 trials, free vs JSON-schema, current model; one extra model for generality | NIM key | R1 |
| E2 | Cloud cost per compilation: round trip, tokens | NIM key | R2 |
| E3 | Evaluator time at 8, 30, 64 rules, live pipeline running | Pi | R3 |
| E4 | Decision-to-relay latency: software timestamp to GPIO edge to contact closure, Analog Discovery 3 on GPIO17 and relay NO, N >= 200 | Relay rig, AD3 | new |
| E5 | 24-hour scripted replay, edge-triggered engine and guard; online vs `unshare -n`, SHA-256 of switching logs | Pi | R4 |
| E6 | Physical offline run: network removed, camera scenario (enter, sit, leave) against 5-8 rules | Relay rig, owner present | new |
| E7 | Egress audit of the **full** authoring path (agent turn + compile), payloads scanned for live descriptor values | NIM key | R5 |
| E8 | Conflict check precision and recall over the final rule set | none | R6 |
| E9 | Retired matcher vs router on the 90 paraphrases: correct, wrong, escalated | Pi | R7 |
| E10 | In-loop baseline actually run: the model decides from the descriptor each tick for a short window; latency, cost, behaviour offline | NIM key | argued only |

Reference rules (`reference_rules.py`) must be read and confirmed by the authors before
E1 is scored; the paper calls them author-written and frozen before any model output.

## 6. Paper layout (6 pages including references)

| Section | Pages | Content |
|---|---|---|
| I Introduction | 0.7 | Problem, gap, claim, three contributions |
| II Related work | 0.9 | Four groups, Table I |
| III Design | 1.2 | Fig. 1 two-lane architecture; schema; gate; evaluator; guard; egress boundary |
| IV Method | 0.5 | Corpus, reference rules, scoring, replay, hardware rig |
| V Results | 1.7 | Table II synthesis; Table III latency (cloud, in-loop, evaluator, relay); Fig. 2 latency CDF; Fig. 3 matcher vs router; offline result in text |
| VI Limitations | 0.3 | One room, scripted descriptors, author corpus, indicator loads, one model family |
| VII Conclusion + refs | 0.7 | About 20 references |

Figures are generated from `data/*.json` by scripts, never drawn by hand from numbers.

## 7. Quality gates before submission

- Every number in the text traced to a file in `data/`.
- Every citation opened; no [A]/[U] source cited for a detail.
- Two simulated reviews (systems venue reviewer, LLM/NLP reviewer), fixes applied.
- Page count, Letter size, fonts embedded, no `\todo`, PDF eXpress if required.
- Artifact: harness and data released (public repo or Zenodo) with the commit hash.
- AI-use disclosure as the venue requires.

## 8. Schedule (if LatinIoT, 28 Nov)

| Dates | Work |
|---|---|
| 4-6 Nov | Answers to Section 9; port harness; reference rules confirmed; literature verification starts |
| 6-12 Nov | E1-E3, E5, E7-E10 runs (software) |
| 10-12 Nov | Hardware session: E4, E6 (about 2 hours with the owner) |
| 10-18 Nov | Full draft, figures |
| 18-22 Nov | Simulated reviews, revision |
| 23-26 Nov | Polish, page and format check |
| 27 Nov | Submit (one day buffer) |

## 9. Decisions (2026-11-04)

1. Venue: department decides. Write to the 6-page IEEE conference format meanwhile; LatinIoT
   (28 Nov) stays the working deadline until told otherwise.
2. Authors: Veera Manikanta, then Sumir Subash Balakrishnan. Emails still needed.
3. JSON-schema constrained output: **adopt** in the deployed compile client (with tests)
   before E1. The paper reports it as the system and free decoding as the ablation.
4. Scope: **two lanes**. Headline on standing rules; immediate commands and the router as a
   supporting result.

---

## 10. Phase 1 results (2026-11-04, Drishti c925f7e + this branch's changes)

Raw files in `data/`. Re-run commands in `evaluation/narada_rs/run_eval.py` header.

### Production changes made (owner approved Q3)

- `nim_client`: rule shape sent as `response_format` (whole rule or `{"error"}`, exactly one of
  `all`/`any`, at least one condition and one action), and **reasoning off** for compilation.
- Why reasoning off: with reasoning on, nemotron-3.5 spent the 2048-token budget thinking and
  about 1 in 4 compilations was cut off (`synth_budget2048_partial.log`): rule creation on the
  live system fails the same way today. 4096 tokens still truncated 2 of 30, median 5 s.
- Tests: 4 new in `tests/garuda_auto/test_nim_client.py`; full `tests/garuda_auto` passes.
- Not yet live: the service runs the main checkout; needs merge into Drishti and a restart.

### E1/E2 synthesis (30 requests x 3 trials; outputs identical across trials)

| Setting | Correct | Valid but wrong | Rejected / broken | Declined | Round trip p50 / p95 | Tokens |
|---|---|---|---|---|---|---|
| Deployed: 3.5-super, schema, no reasoning | 84 (93%) | 6 | 0 | 0 | 0.9 / <=3.1 s | 893 |
| No schema | 79 | 8 | 3 | 0 | 0.9 / <=5.4 s | 868 |
| Reasoning on, 4096 | 65 | 3 | 11 truncated | 11 | 5.5 / ~30 s | ~2280 |
| 3-ultra-550b, deployed setting | 90 (100%) | 0 | 0 | 0 | 2.4 / <=6.1 s | 892 |

Valid-but-wrong (deployed): c05 ("after nine"), c07, c22 class: off-by-one hours and an added or
dropped presence condition. These reach the confirm step; none is malformed.

### E3 evaluator time (garuda-web live, camera pipeline running, service ~47% CPU, SoC 55-59 C)

8 / 30 / 64 rules, 20,000 ticks each: median 12 / 44 / 92 us, p99 19 / 70 / 137 us, max 2.0 /
0.7 / 0.7 ms. Medians match an idle run; only the tail grows under load. Either way the 0.5 s
tick, not evaluation, sets reaction time.

### E4 decision-to-contact on the real relay (2026-11-05, 200 cycles, production path)

Path: RuleEngine.evaluate (edge-triggered) -> DeviceRouter.set (switch guard) -> RelayBank
(gpiozero/lgpio) -> HW-281 channel 2. Contact wired as a dry contact into GPIO24; GPIO edges
kernel-timestamped on the same monotonic clock as the decision mark. garuda-web stopped.

| | Decision to GPIO write | Decision to first contact edge | Bounce after first edge |
|---|---|---|---|
| On (n=88) | 0.13 ms p50, 0.18 ms p99 | 3.90 ms p50, 3.95 ms p99, 4.47 ms max | 0.44 ms, every switch |
| Off (n=88) | 0.13 ms p50, 0.16 ms p99 | 3.59 ms p50, 3.62 ms p99, 3.77 ms max | 0.05 ms, every switch |

- Rule evaluation and the software path take about 0.13 ms; the relay's mechanics take the
  remaining ~3.5-3.8 ms. Reaction time is therefore set by the 0.5 s tick, not by actuation.
- The switch guard refused 112 of 200 "on" requests (the test asked faster than 10 per minute);
  all 1,380 contact edges fall inside switches that happened, none during a refusal: the guard
  holds physically, not only in software.
- Found on the way: gpiozero 2.0.1 picks gpiochip0/4 and ignores its chip argument; on this
  Pi's boots the header is gpiochip15, so the harness opens the chip by its label.

### E6 physical offline test (2026-11-05, live service, camera, Wi-Fi removed)

Wi-Fi (the Pi's only network) switched off for 600 s by a systemd unit that restored it
itself; 120/120 reachability checks in the window failed. The owner walked in and out
in front of the camera; 4 temporary rules (lamp on when occupied, lamp off after 20 s empty,
fan on after 30 s occupied, fan off when empty), removed afterwards.

- Lamp: 9 rule-driven switches while offline (4 on, 5 off), every one in the service's
  actuation log with its rule id and matched by the relay drive pin (GPIO17) changing within
  0.1 s. No switch missed, none spurious.
- Fan: never commanded. Person detection dropped the owner within the 30 s dwell each time
  (three presences ended about 0.6 s after detection), so the dwell rule never held. A
  perception limitation, not an actuation one; say so in Limitations.
- Relay-contact evidence for E6 is missing: a stray second wire on pin 18 made the sense
  line unreliable (bursts of spurious edges, no closure). Contact timing stands on E4.
- Data: data/e6/ (monitor.jsonl, events.log, actuations_window.jsonl).

### E5 24-hour replay (172,800 ticks, edge-triggered engine + switch guard)

- Online vs no network interface (`unshare -n`): **identical SHA-256** of the switching log.
- Deployed rules, stated thresholds: 52 firing edges matched, 1 false, 2 missed (19/21 rules identical).
- Vague thresholds: 11 matched, 7 false, 4 missed (model picks its own "dark"/"warm").
- Lamp state agrees with the reference 83.6% of ticks, fan 93.9%. Lower than October (95.8%+)
  because an edge-triggered rule does not re-assert, so one early divergence persists. Worth a
  sentence: edge triggering fixes re-firing but makes errors last longer.
- Switch guard held back 0 switches in the scripted day.

### E7 egress over the whole authoring path (30 Narada turns, canary readings in the descriptor)

- Agent requests: 72/72 carried live values (device states, occupancy, person count), ~16.9 kB each.
- Compile requests: 25/25 carried none, ~3.8 kB each.
- Turns: 25 drafted a rule, 3 became schedules (pure time), 1 correctly declined (no humidity
  sensor), **1 claimed a draft it never made (c06)**: a false claim in the conversational lane.
- Authoring time per turn: median 3.0 s, p95 11.0 s.

Paper wording: the compile boundary is schema-only and auditable; the conversational front end
sends house state, as every assistant of this kind does.

### E8 conflict check (unchanged logic)

127 opposed pairs, 62 can co-hold; 98 flagged: precision 63%, recall 100%. Added one by one,
25 of 30 rules raise a prompt, 18 name a rule that really overlaps.

### E9 request reuse

- Retired embedding matcher (reproduces October): 68/90 correct, 13/90 wrong rule, 81/120 new
  intents swallowed, 2.8 s per match (54 ms with cached vectors).
- Router (deployed): 49/50 hand-written set, 636/717 held-out NIM-written set, median 12.8 ms
  (17.7 ms on the larger set), wrong-while-sure 8/717.

### E10 model in the loop (59 sampled decisions over the same day, reasoning off)

- Correct 48/59 (81%); at switching moments only 11/19 (58%); 3 spurious switches at quiet moments.
  Typical error: switches both devices when one rule asks for one.
- Decision time median 0.4 s, p95 2.5 s, max 15.6 s; 9/59 slower than the 0.5 s tick.
- ~790 tokens a decision; at 2 Hz that is ~136 M tokens a day. Offline it cannot decide at all.

### Still open

- c06 false-draft claim: fix in `claims.py`/agent (separate from the paper).

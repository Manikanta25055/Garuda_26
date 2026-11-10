# Drishti paper: measured results and paper plan

Date: 2026-10-06. Companion to `drishti_literature_and_narrative.md`.

All numbers below were measured today on the Pi 5 (`ai`), from the live checkout
(commit a6b7cd4), with the harness in `evaluation/narada_rs/`. Raw outputs are in
`evaluation/out/narada_rs/*.json`. Nothing touched the live rule store, the device registry
or a GPIO pin: rules went to temp files and the registry was an in-memory lamp and fan.

Re-run:

```
python3 -m evaluation.narada_rs.run_eval synth --trials 3
python3 -m evaluation.narada_rs.run_eval synth --trials 3 --constrained
python3 -m evaluation.narada_rs.run_eval matcher
python3 -m evaluation.narada_rs.run_eval matchlat
python3 -m evaluation.narada_rs.run_eval reflex
python3 -m evaluation.narada_rs.run_eval scenario
sudo unshare -n sudo -u manikanta python3 -m evaluation.narada_rs.run_eval scenario --tag _offline
```

---

## 1. Results

### R1. Synthesis outcome (30 requests x 3 trials = 90 compilations)

Model: `nvidia/nemotron-3-super-120b-a12b`, temperature 0.1. "Correct" means the rule fires
on the same descriptor states as a reference rule written before any output was seen
(`reference_rules.py`), over a grid of consistent states around every threshold. Where the
utterance gives no number ("dark", "warm"), the threshold is not held against the model.

| Outcome | Deployed client | With JSON-schema output (ablation) |
|---|---|---|
| Correct | 58 (64%) | 74 (82%) |
| of which exact / threshold-free | 43 / 15 | 60 / 14 |
| Valid but wrong (reaches the confirm step) | 5 (5.6%) | 7 (7.8%) |
| Malformed, stopped on device | 23 (26%) | 0 |
| Model declined (asks for a threshold) | 4 (4.4%) | 9 (10%) |
| Malformed rule stored | 0 | 0 |
| Correct per trial | 17, 21, 20 | 24, 25, 25 |

- All 23 malformed outputs have one cause: the model writes `{"field": "hour", ">=": 23}`
  or `{"field":"hour",">=","value":23}`, dropping the `"op"` key. 14 were refused by the
  validator, 9 were not valid JSON. None was stored.
- The NIM endpoint accepts `response_format: json_schema`. Sending the rule shape removes
  the whole class. This is **not** in the deployed client; it is a harness-only ablation.
- Valid-but-wrong cases (the ones only the confirm step can catch): `hour > 21` or
  `hour >= 22` for "after nine" (c05); `zone == door` without the walking posture (c07);
  an added `zone == door`, or `owner_event == arrived`, for "someone walks in" (c29);
  `hour >= 18` or `security == night_presence` for "after dark" (c14).
- Under the looser criterion written into the corpus header (devices match, required
  fields present), 61 of 63 stored rules pass (deployed) and 76 of 81 (constrained).

### R2. Cloud cost of one compilation

| | Deployed |
|---|---|
| Round trip, median / p95 / max | 3.8 s / 13.8 s / 28.8 s |
| Tokens per request (mean) | 1,172 (about 590 prompt, rest mostly reasoning) |
| Request size (mean) | 2.9 kB |

This is also the floor for an LLM-in-the-loop baseline: every decision would pay at least
this.

### R3. Reflex tier

`RuleEngine.evaluate` on the Pi 5 while the live detection pipeline was running, 20,000
ticks each:

| Rules | Median | p99 | Max |
|---|---|---|---|
| 8 | 12 us | 17 us | 0.53 ms |
| 30 | 42 us | 70 us | 0.48 ms |
| 64 (the cap) | 88 us | 135 us | 0.48 ms |

So rule evaluation is negligible; reaction time is set by the 2 Hz tick (worst case 0.5 s)
plus the relay. **Relay switching time is not measured yet** (needs hardware, see section 3).

### R4. Scripted day replay and offline check

A scripted 24-hour day (172,800 ticks at 2 Hz, `scenario.py`), production engine, device
state fed back from its own actions. 28 of 30 reference rules fire at least once.

- Rules with a stated threshold: firing edges identical to the reference for 15/15, 15/15
  and 14/15 stored rules (deployed trials); 20/21, 21/21, 21/21 (constrained).
  Across deployed trials: 117 matched, 1 false, 1 missed firing edge.
- Rules with an unstated threshold differ, as expected, because the model picks
  `ambient_luma < 30` or `< 50` where the reference uses 60.
- Lamp and fan state agree with the reference rule base (same intents) for 95.8% to 100%
  of ticks.
- **Offline:** the same replay inside a network namespace with no interfaces
  (`unshare -n`, connect test fails) gives a byte-identical switching log (same SHA-256).

Limits to state in the paper: descriptors come from a script, not the camera; lamp state
does not feed back into ambient luma; no relay was switched.

### R5. Egress audit

All 180 outbound requests contained exactly three keys: `schema`, `already_known`,
`instruction`. The harness has no live descriptor, so this confirms the structure of the
payload, not the absence of live values in the running service. A capture on the live
service is still worth doing.

### R6. Conflict check

Over the 30 reference rules there are 127 rule pairs with opposite actions on one device.

- 62 can really hold at the same time. The check flags all 62 (no misses).
- It flags 98 in total, so 36 flags are spurious (63% precision). Cause: it compares
  conditions field by field and cannot see that `occupancy == empty` excludes
  `person_count > 2` or `zone == desk`.
- Added one by one, 25 of 30 rules raise a conflict prompt, 22 of them with a real
  conflict. With two devices and thirty rules, conflicts are the normal case.

### R7. Paraphrase matcher (negative result)

Store holds the 30 base utterances; the 90 paraphrases are queried. Threshold 0.72 is the
code default and was not tuned.

| | MiniLM embeddings | rapidfuzz |
|---|---|---|
| Correctly matched (cloud call avoided) | 68 / 90 (76%) | 25 / 90 (28%) |
| Matched to the wrong rule | 13 / 90 (14%) | 21 / 90 (23%) |
| Escalated to cloud | 9 / 90 | 44 / 90 |
| New intent wrongly treated as known (leave-one-intent-out) | 81 / 120 (68%) | 49 / 120 (41%) |
| Random 120-request stream: cloud calls (ideal 30) | 40.8 mean | 58.3 mean |
| Same stream: wrong matches | 39.6 mean | 47.5 mean |
| Match time on Pi, 30 rules, as deployed | 3.5 s median | 0.11 ms |
| Match time with rule vectors cached (what-if) | 70 ms median | n/a |

- In a narrow domain every request looks like "turn the fan off when ...", so sentence
  similarity cannot separate "empty for five minutes" from "empty for an hour".
- Raising the threshold to 0.84 cuts wrong matches to 1/90 but only 23/90 are then
  matched, and 12/120 new intents are still swallowed.
- The deployed embedding matcher re-encodes every stored utterance on every query, so it
  takes as long as the cloud call it is meant to avoid.

**Consequence for the paper:** paraphrase suppression cannot be a headline. It becomes an
honest negative finding with a trade-off curve, which is consistent with the literature
(GPTCache reports wrong hits too) and helps credibility.

---

## 2. Paper plan

**Claim (unchanged, now backed):** a cloud model can author rules without sitting on the
actuation path, and the on-device gate fails closed.

**Three contribution bullets, each tied to a result:**
1. Architecture with an LLM-free firing path. Evidence: R3, R4 (offline identical), R2 as
   the in-loop cost.
2. A registry-derived gate measured end to end. Evidence: R1 (0 malformed rules stored out
   of 90; 5.6% valid-but-wrong left for the confirm step), R6.
3. Two findings others can use: constrained output lifts correct compilations from 64% to
   82% (R1), and utterance-similarity matching is unsafe for skipping the cloud in a narrow
   command domain (R7).

**Section outline (6 pages):**

| Section | Pages | Content |
|---|---|---|
| I Introduction | 0.7 | Problem, gap, claim, three bullets |
| II Related work | 0.9 | Four short groups + comparison table (10 rows) |
| III Design | 1.2 | Fig. 1 architecture; rule schema; validator; conflict check; egress boundary; three lanes |
| IV Evaluation method | 0.6 | Corpus, reference rules, behavioural scoring, scripted day, what is replay and what is not |
| V Results | 1.8 | Table: R1. Table: R2+R3. Fig: R7 threshold curve. Table or text: R4, R6 |
| VI Limitations | 0.4 | One room, scripted, authors' corpus, single model, no relay timing yet |
| VII Conclusion, refs | 0.4 | About 20 references |

**Figures and tables:** Fig. 1 architecture (two tiers, egress boundary marked); Fig. 2
matcher trade-off curve (correct vs wrong match vs threshold, both backends); Table I
related work; Table II synthesis outcomes; Table III latency (cloud vs reflex).

**Order of work:**
1. You: answer the parked items in section 4 (venue and authors first).
2. Hardware session (section 3), about one hour with the relay board connected.
3. Me: IEEEtran conference skeleton, figures from the JSON files, full draft with the
   human-academic-prose method and prose lint, compile and page check.
4. Citation pass: open each full text marked [A] or [U] in the literature file before it
   is cited. ChatIoT is still unread (ACM paywall); get it through the library.
5. Simulated review (`/ars-reviewer`), fix, disclosure statement, submit.

---

## 3. Still missing (needs you or hardware)

| Item | Why it matters | Effort |
|---|---|---|
| Decision-to-relay time on the real relay (timestamp before `set()`, scope or AD3 on the relay output) | Completes the latency table | 30 min |
| Real offline test: pull the network on the live Pi, trigger two rules, check the actuation log | Turns R4's software check into a physical one | 15 min |
| Short real-camera run (enter, sit, leave) against 5 to 8 rules | Shows the camera descriptor works, not only the script | 30 min |
| Payload capture on the live service | Completes R5 | 10 min |
| LLM-in-the-loop baseline (B2) | Currently argued from R2, not run. One short run, or say so plainly | optional |

## 4. Parked doubts

1. Venue and deadline. LatinIoT 2027 (28 Nov 2026) fits the format; confirm what the
   department accepts.
2. Authors and order.
3. Should the deployed client adopt `response_format: json_schema`? It is a few lines in
   `nim_client.py` and would raise correct compilations to about 82%. I did not change
   production code. If adopted, the paper reports it as the system and the current
   behaviour as the ablation.
4. Every synthesised rule has `cooldown_s = 0` (63 of 63). The prompt does not say what
   cooldown to use, so a rule re-issues its action every tick while it holds. Harmless for
   a relay, noisy for the actuation log. Intended?
5. The embedding matcher takes 3.5 s per query at 30 rules. Caching rule vectors would
   make it about 70 ms. Also not changed.
6. The reference rules in `reference_rules.py` are mine. Please read the 30 lines and
   confirm they say what you meant; the paper will call them author-written.
7. System name in the paper, and AR933 patent overlap.
8. Local repo is 120 commits behind `origin/Drishti` and has one modified plan file I did
   not touch. I did not pull or commit. New files are untracked.

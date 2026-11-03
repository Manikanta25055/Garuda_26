# Drishti conference paper: literature review, gaps and narrative

Date: 2026-10-06. Source: deep-research run (23 sources fetched, 114 claims extracted, 25 put
to a 3-vote adversarial check, 23 confirmed, 2 refuted) plus my own spot checks of AutoIoT,
Harmony, MeanCache and ChatIoT abstracts.

Marking used below: **[V]** = passed the 3-vote check. **[A]** = read from the abstract or
one extraction pass only; re-read the full text before citing a detail. **[U]** = not verified.

---

## 1. Verdict in five lines

1. "LLM as a rule compiler" is **not new on its own**. ChatIoT, Giudici et al., Sasha, FARM and
   AutoIoT all turn one sentence into a stored trigger-action rule.
2. Conflict checking of LLM-written rules is **not new** either (AutoIoT, 2024), and
   paraphrase matching to skip an LLM call is prior art (LLMind, GPTCache, MeanCache).
3. What no checked paper reports is the **measured behaviour of the whole path**: a cloud model
   that never sits on the actuation path, a typed on-device gate on its output, and physical
   loads that keep switching correctly with the network down.
4. So the paper should stress **one** thing: a validated compile-once architecture with an
   offline reflex tier, and prove it with measurements nobody else gives (three-way synthesis
   outcome, decision-to-relay latency, offline actuation, suppression rate with false matches).
5. The paper currently has **no result numbers**. `evaluation/narada_rs/` holds only the
   corpus. The evaluation runs are the critical path, not the writing.

---

## 2. Section to stress

**Stress the Evaluation, framed by a short, sharp Related Work table.**

Reason: every design idea in Drishti has a named precedent, so a reviewer will not accept a
novelty-by-architecture story. The Access rejection notes (`REVIEW_FIX_PLAN.md`, P1-2) already
flagged "novelty by enumeration". The defensible position is a measurement gap: prior systems
describe rule synthesis but do not report what happens after the rule is stored.

Page budget for 6 pages: Intro 0.75, Related Work + table 1.0, Design 1.25, Evaluation 2.25,
Limitations 0.4, Conclusion + refs 0.35.

---

## 3. Related work, grouped the way the paper should present it

### 3.1 LLM in the control loop

| Work | What it does | What it leaves open |
|---|---|---|
| Sasha, King et al., Proc. ACM IMWUT 8(1), 2024, arXiv:2305.09802 **[V]** | Multi-step LLM pipeline per command; emits (trigger, action) JSON for persistent goals | No rule runtime described; hands-on study (N=7) used immediate goals only. Reports about $0.02-0.03 per command at 2023 prices. Documents hallucinated devices and bad trigger values, fixed by more prompting |
| SAGE, Rivkin et al., arXiv:2311.00772 **[V]** | LLM writes Python *condition* code that is polled every few seconds | When the condition turns true the LLM agent runs again to plan the action. Reads state through the SmartThings cloud. Closest system; call it "partially compiled" |
| IoTGPT, Yu et al., arXiv:2601.04680 **[A]** | GPT-4o agent with task memory over SmartThings | Lists local inference as future work; commands go to the cloud |
| Birkmose et al., arXiv:2502.12923 **[V]** | On-device Qwen2.5-0.5B, one JSON action per utterance | 5-6 s per query on a CPU-only device; no stored rules |
| Harmony, Yin, Zhang, Kawahara, arXiv:2410.14252 **[A]** | Local Llama3-8B assistant, offline, stores persistent actions as rules | Needs hardware that can host an 8B model; virtual environments and user studies, no physical loads or latency in the abstract |
| HomeLLaMA, Huang et al., arXiv:2507.08878 **[A]** | On-device small model; optional "PrivShield" sends only less-sensitive queries to the cloud | Model stays in the loop; no latency, offline or actuation metrics in the abstract |
| AdaHome, Lim, Li, Stein, arXiv:2607.18034 **[A]** | Local small model with intent-aware routing | Per-command inference; metrics seen only in a search snippet |

### 3.2 One-time rule or program synthesis

| Work | What it does | What it leaves open |
|---|---|---|
| ChatIoT, Gao et al., Proc. ACM IMWUT 8(3), 2024, DOI 10.1145/3678585 **[V abstract]** | Zero-code TAP generation, deployed to a TAP runtime; 26.1-84.9% fewer tokens, 91.57% accuracy in a user study | Full text not opened. Validation, conflict check and offline behaviour of the runtime are unknown. **Biggest open risk** |
| Giudici et al., arXiv:2505.02802 **[V]** | GPT/LLaMA/Mistral emit Home Assistant automation JSON | Only check is whether Home Assistant accepts it; sends a home template with energy data to the cloud; offline not evaluated |
| AutoIoT, Cheng et al., arXiv:2411.10665 **[A, spot-checked]** | LLM rule generation **plus formal-verification conflict detection and avoidance** for HomeKit/SmartThings | Abstract reports no latency, offline, privacy or actuation evaluation |
| FARM, Badalov and Yoon, arXiv:2601.15687 **[A]** | NL to IFTTT/Zapier applet, with a configuration-verification step; 81% joint accuracy on clean input | Cloud web services only; about 1 in 5 applets wrong |
| LLMind, Cui et al., arXiv:2312.09007 **[V abstract]** | Per-task control script via an FSM; stores scripts and retrieves them by embedding similarity | Scripts, not standing rules; no reuse rate reported |
| AutoIOT, Shen et al., arXiv:2503.05346 and GPIoT, arXiv:2503.00686 **[V]** | Synthesize AIoT programs that run locally; claim privacy and token savings from local execution | General sensing programs, not home rules. Name clash with AutoIoT above: cite by arXiv id |

### 3.3 Rule safety before LLMs

- iRuler, Wang et al., ACM CCS 2019, DOI 10.1145/3319535.3345662 **[A]**: SMT and model
  checking over IFTTT rules; its taxonomy already has an "action conflict" class, which is what
  Drishti's same-device, opposite-state check is.
- AutoTap, Zhang et al., ICSE 2019 **[A]**: synthesizes and repairs TAP rules from LTL
  properties.
- IoTGuard, Soteria, IoTSan **[U]**: not reached in this pass. Read before citing.

### 3.4 Cutting cloud calls by matching earlier requests

- GPTCache, Bang et al., NLP-OSS 2023 **[A]**: caches LLM *answers* by embedding similarity;
  about 0.2-0.3 s on a hit against about 3 s; roughly 4.5% of hits were wrong in one test.
- MeanCache, Gill et al., IEEE IPDPS 2025, DOI 10.1109/IPDPS64566.2025.00117 **[A,
  spot-checked]**: cache on the user's device; about 31% of queries are repeats; better
  precision than GPTCache.
- Takeaway: a suppression rate must be reported together with a **false-match rate**.

### 3.5 Camera as the context sensor

- Calautit et al., Energy Proceedings vol. 68, 2026 **[A]**: review plus Raspberry Pi case
  study of vision occupancy counting; asks for decision-oriented benchmarks.
- MiMo-VL-Miloco-7B, Xiaomi, arXiv:2512.17436 **[A]**: 7B home vision-language model on the
  edge. Opposite design point: a large model in the perception loop.
- No checked paper feeds a compact camera-derived state vector into language-authored rules.
  This angle is thinly covered, so word it "to our knowledge".

---

## 4. Research gaps, written as trade-offs

**G1. Open language versus an LLM-free firing path (main gap).**
In-loop agents accept any sentence but need a model at decision time (Sasha, IoTGPT, and the
on-device ones at 5-6 s per query). SAGE compiles the condition but re-plans the action with
the LLM on each firing. ChatIoT and Giudici et al. hand a rule to a runtime, but none reports
decision-to-actuation latency or behaviour with the network down.
Drishti: both condition and action are compiled; firing needs no model and no network.
Reviewer attack: "Home Assistant already runs automations offline." Answer: nobody measured
it for LLM-authored rules; we do.
Strength: good, if the offline and latency numbers exist.

**G2. Flexible generation versus trust in what gets stored.**
Sasha fixes hallucinated devices with more prompting. Giudici et al. rely on platform
acceptance. AutoIoT adds formal conflict detection but reports nothing on wrong-but-valid
rules.
Drishti: allowlist derived from the device registry, conflict check, confirm-before-save, and
an honest split into correct / valid-but-wrong / rejected.
Reviewer attack: "AutoIoT and iRuler already do conflict detection, and stronger." Answer:
concede it; the contribution is the measured residual error after the gate, not the check.
Strength: medium. Do not call the conflict check novel.

**G3. Strong cloud model versus what leaves the house.**
Local-model systems (Harmony, HomeLLaMA, GPIoT) get privacy by giving up the large model or
needing heavy hardware. Cloud systems send home configuration, profiles or energy data
(Giudici et al.; HomeLLaMA's own motivation).
Drishti: keeps the cloud model but sends only utterance, schema, device list and rule
summary, never live values.
Reviewer attack: "HomeLLaMA PrivShield already filters what goes out." Answer: that is
per-query filtering with a model still in the loop; ours is a fixed boundary that can be
audited by logging every outbound payload.
Strength: medium, needs an egress audit table to count.

**G4. Cloud dependence over time (measurement gap only).**
LLMind and the caching work match paraphrases, but none reports how cloud calls fall as a
rule base grows in a home setting.
Drishti: suppression rate over 30 requests x 3 paraphrases, two matcher variants, with
false matches.
Strength: supporting result, not a headline.

**G5. Camera-derived state as rule vocabulary.**
Plausibly open, under-verified. Use one sentence, not a contribution bullet.

---

## 5. Narrative

**One-sentence claim.** A cloud language model can author home-automation rules without ever
sitting on the actuation path, provided its output passes an on-device typed gate, and the
resulting system keeps switching loads correctly in under 100 ms with the network unplugged.

**Arc.**
1. Problem: assistants that put a model on every decision pay seconds of delay and a
   per-command cost, and stop when the network does.
2. What others did: moved to rule synthesis, or moved the model on-device.
3. What is still missing: nobody reports what the stored rule does afterwards. No
   decision-to-relay latency, no offline actuation test, no count of rules that pass
   validation but mean the wrong thing.
4. Idea: treat the model as a compiler with a strict type checker in front of the rule
   store, and a 2 Hz local evaluator behind it.
5. Evidence: five measurements on real relays (section 6).
6. Honest limits: one room, one camera, scripted corpus, authors as speakers.

**Working title options.**
- "Compile Once, Act Locally: Validated Language-Authored Rules for Offline Edge Home Automation"
- "Keeping the Language Model Off the Actuation Path: A Measured Edge Home-Automation System"

**Contribution bullets (three, not five).**
1. An architecture in which a cloud model compiles spoken or typed instructions into typed
   rules that an on-device evaluator runs with no model or network at firing time.
2. A registry-derived validation gate with conflict check and user confirmation, and a
   three-way measurement of what it lets through.
3. Measurements on a Raspberry Pi 5 with physical loads: decision-to-relay latency against an
   in-loop baseline, offline actuation, and cloud-call suppression with false-match rate.

**Drop or soften.**
- Drop "first to use an LLM as a compiler" and any "first NL-to-rule" wording.
- Drop conflict detection as a novelty. Cite iRuler and AutoIoT.
- Soften paraphrase matching to a measured property. Cite LLMind, GPTCache, MeanCache.
- Soften "privacy-preserving" to the specific egress boundary plus audit.
- Do not write "orders of magnitude faster". Give the measured ratio and say the comparison
  is rule evaluation against intent parsing.
- Do not cite Sasha latency numbers (35.83 s / 46.69 s): that claim was refuted 0-3.
- Never say "the closest prior work"; say "among the closest".
- Keep Garuda detection results out. One sentence and one citation at most. The Hailo
  detector is a sensor here, not a contribution.

---

## 6. Evidence the paper needs (none exists yet)

| # | Measurement | Fills | Status |
|---|---|---|---|
| E1 | Synthesis outcome over 30 requests: correct / valid-but-wrong / rejected, with rejection reasons | G2 | corpus ready, not run |
| E2 | Decision-to-relay latency distribution (reflex tier) vs cloud round trip for baseline B2 | G1 | not run |
| E3 | Offline test: network pulled mid-scenario, every rule still fires | G1 | not run |
| E4 | 60-minute scripted room scenario: correct, false and missed actuations | G1 | not run |
| E5 | Paraphrase suppression and false-match rate, MiniLM vs rapidfuzz, threshold fixed beforehand | G4 | not run |
| E6 | Egress audit: log of every outbound payload, showing no live values | G3 | not run |
| E7 | Tokens and cost across the corpus | support | not run |

E1, E5, E6 and E7 need only the Pi and the NIM key. E2, E3 and E4 need the relay board and
loads wired.

---

## 7. Venue leads (not confirmed)

- IEEE LatinIoT 2027, Santiago, 28-30 April 2027. Deadline 28 Nov 2026, 6 pages, IEEE
  Xplore **[A]**. Format matches.
- IEEE WoWMoM 2027, deadline reported as 1 Dec 2026 **[U]**; selective, probably too hard.
- IEEE ICCE 2027, Las Vegas, 8-11 Jan 2027, has an IoT and edge-AI track; deadline likely
  already passed **[U]**.
- IoTBDS 2027 (SciTePress, not IEEE), 17 Nov 2026 **[U]**.

Check each call page before choosing.

---

## 8. Parked doubts (answer when back)

1. Which conference does the department accept for the minor project, and is there a hard
   date? Scopus-indexed IEEE only, or any IEEE Xplore venue?
2. Author list and order for the Drishti paper. The init draft lists Sumir first. I have not
   changed anything.
3. Is the relay board, lamp and fan hardware wired and reachable? E2-E4 depend on it.
4. Is NIM credit still available for the 30-request corpus?
5. System name in the paper: Drishti, Narada-RS, or neutral? AR933 patent overlap (spec
   section 3) should be re-checked against the three bullets in section 5.
6. Template: the folder has only the BibTeX style files. I plan to add the standard
   `IEEEtran.cls` conference skeleton unless the venue supplies its own.

## 9. Before any citation goes into the .tex

- Open the ChatIoT full text. If it has validation, conflict checks or an offline runtime, G1
  and G2 narrow and section 5 must be reworded.
- Re-read LLMind, Harmony and AutoIoT full texts for the details marked [A].
- Confirm venues for SAGE, LLMind, AutoIOT, GPIoT and the Sasha DOI.
- Read IoTGuard, Soteria and IoTSan, or leave them out.

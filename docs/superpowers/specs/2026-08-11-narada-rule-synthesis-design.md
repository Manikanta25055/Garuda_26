# Design: Spoken Rule Synthesis for Edge Home Automation

**Date:** 2026-08-11
**Working title:** Narada-RS — teaching a house by talking to it
**Context:** Minor project (8 credits) extending Project Garuda. Target output: one conference paper.
**Platform:** Raspberry Pi 5 + Hailo-8L AI HAT (already owned), single camera, single room.

---

## 1. The problem

Every current "LLM runs your smart home" system puts the language model in the control loop.
Each decision is a network round trip. Three consequences follow: actuation latency in
seconds, a per-decision cost that never goes away, and a house that stops working when the
network does.

Garuda's existing voice assistant, Narada, has a milder version of the same problem. It
matches roughly fifteen hardcoded phrases locally (`match_rule_based_command`,
`basic_pipelines/Garuda_web.py:1744`) and forwards anything else to a cloud LLM as text
(`query_local_llm`, `:1800`). The cloud answer is applied once and discarded. Ask the same
thing tomorrow and it goes to the cloud again. The local vocabulary never grows.

Narada also has no hands. It flips software mode flags. It cannot switch a light.

## 2. The idea

Take the language model out of the control loop and make it a **compiler**.

When the user speaks an instruction the system does not recognise, the utterance text goes to
the cloud model, which returns not a reply but a **persistent rule** — a predicate over scene
state paired with an action over devices. The rule is validated on-device and written into a
local rule base. From then on it is evaluated locally, at reflex speed, with no network
involved, indefinitely.

The house is taught once by voice, then runs itself.

**Measurable consequence:** teaching a rule should suppress future cloud calls for the same
intent, including differently-phrased requests. That suppression rate is the headline result.

## 3. Relationship to AR933 (patent) — what is and is not new

This matters for both the paper's novelty claim and for staying outside the filed scope.

**Already claimed by AR933, therefore not a contribution here:**

- Item 14: local wake word plus local command match, with unmatched commands escalated to an
  external LLM as text. Repointing that escalation from Groq to NVIDIA NIM is a vendor swap —
  both are OpenAI-compatible endpoints. Roughly a config change in `query_local_llm`.
- Offline STT via Vosk (`_transcribe_offline`, `:1943`). Already implemented, already the
  privacy contract.
- The detection cascade, anti-spoof, privacy blur, mode flags, alerting.

**New, and the actual contribution:**

1. The cloud model emits **persistent rules**, not one-shot responses. `_apply_llm_result`
   currently mutates mode flags once and forgets; the replacement writes a durable artifact.
2. Rule predicates read **camera-derived scene state and physical sensors** — occupancy,
   posture, zone, ambient luma, temperature — not only software mode flags.
3. Rules fire **autonomously and drive physical loads**. They are evaluated continuously,
   not only when spoken to.
4. **Cloud dependency shrinks as the rule base grows**, measured by paraphrase suppression.

Items 1–4 together are outside AR933 item 14. The novelty claim is the architecture, not the
use of a VLM or LLM.

## 4. Architecture

Two tiers on distinct timescales.

### 4.1 Reflex tier — local, continuous, sub-100 ms

Runs on the Pi alongside the existing GStreamer pipeline. Never touches the network.

- Consumes detections from the existing primary YOLO path.
- Derives the **scene descriptor** (§5) once per evaluation tick (target 2 Hz — actuation
  does not need frame rate).
- Evaluates every enabled rule in the rule base against the descriptor.
- Drives relay channels on match, subject to per-rule cooldown.

### 4.2 Synthesis tier — cloud, event-triggered, seconds

Invoked only when a user utterance fails local matching.

- Input: utterance text, the scene-descriptor **schema** (field names and legal values), the
  device list, and the existing rule base in summary form. No frames. No audio. No history of
  what the camera saw.
- Output: a single structured rule object, or a refusal.
- Provider: NVIDIA NIM, OpenAI-compatible chat completions with JSON-constrained output.

### 4.3 Local matcher

Decides whether an utterance needs the cloud at all. This component is what makes the
suppression claim real, so it is evaluated in its own right (§8).

- Primary: sentence embeddings (all-MiniLM-L6-v2, ~80 MB, Pi 5 CPU, ~20 ms) over each rule's
  stored `source_utterance`. Cosine similarity above threshold means local hit.
- Fallback: fuzzy token overlap (rapidfuzz).

Comparing the two is a small ablation and belongs in the paper.

## 5. The scene descriptor

The complete vocabulary a rule predicate may reference. This doubles as the validator
allowlist — a rule naming any field outside this table is rejected.

| Field | Type | Source |
|---|---|---|
| `occupancy` | `empty` \| `occupied` | primary detector |
| `person_count` | int | primary detector |
| `occupancy_duration_s` | int | time in current occupancy state |
| `zone` | `none` \| `desk` \| `door` \| `center` | bbox centroid vs. configured polygons |
| `posture` | `none` \| `standing` \| `seated` \| `walking` | pose keypoints |
| `ambient_luma` | 0–255 | mean frame luma |
| `temperature_c` | float | DHT22 |
| `humidity_pct` | float | DHT22 |
| `hour` | 0–23 | system clock |
| `lamp_state` / `fan_state` | `on` \| `off` | relay driver |

Three zones only. More zones is calibration work with no research payoff.

## 6. Rule schema

```json
{
  "id": "r_014",
  "source_utterance": "turn the fan off when nobody's been in the room for five minutes",
  "when": {
    "all": [
      {"field": "occupancy", "op": "==", "value": "empty"},
      {"field": "occupancy_duration_s", "op": ">=", "value": 300}
    ]
  },
  "then": [{"device": "fan", "action": "off"}],
  "cooldown_s": 60,
  "enabled": true,
  "created_at": "2026-08-11T20:58:00+05:30"
}
```

`when` supports `all` and `any`, one level deep. No nesting beyond that.

### 6.1 Validation — never trust the model's output

Every synthesised rule passes all of these before it is stored, or it is rejected and the
user is told why:

- Every `field` is in the §5 table; every `op` is in `== != < <= > >=`; every value matches
  the field's declared type and legal range.
- Every `device` and `action` is in the device allowlist.
- Numeric bounds are clamped (`occupancy_duration_s` capped at 24 h, and so on).
- Total rule count is capped at 64.
- **Conflict check:** if an existing rule drives the same device to the opposite state under
  a satisfiable overlapping predicate, the new rule is not silently stored — the conflict is
  surfaced to the user for confirmation.

Rule storage is JSON on disk, loaded at boot, so learned rules survive restarts.

## 7. Egress boundary

Stated precisely, because it is a claim in the paper.

**Crosses to NVIDIA:** the transcribed utterance text, the descriptor *schema*, the device
list, and a summary of existing rules.

**Never crosses:** frames, crops, keypoint arrays, audio, and live scene-descriptor *values*.
The cloud model is told what fields exist, never what they currently read.

That last exclusion is deliberate and stricter than necessary. It means the cloud never
learns anything about the occupants, only about the vocabulary of the house. It costs nothing
— a compiler does not need runtime values — and it makes the privacy claim need no asterisk.

**Offline behaviour:** no network means no *new* rules. Every existing rule continues to fire.
This is tested explicitly (§8).

## 8. Evaluation

Rule authoring is **scripted and replayed**, not organically collected. The paper must say so.

### 8.1 Corpus

Thirty natural-language rule requests spanning the descriptor vocabulary, plus three
paraphrases of each (120 utterances total). Paraphrases are written before any results are
seen.

### 8.2 Metrics

1. **Synthesis success rate** — of 30 requests, how many produce a rule that is valid, safe,
   and semantically correct against a hand-written reference. Three-way outcome: correct /
   valid-but-wrong / rejected by validator.
2. **Paraphrase suppression rate** — the headline. After learning a rule from phrasing A, how
   many of A′, A″, A‴ match locally instead of escalating. Reported for both matcher variants.
3. **Actuation correctness** — a scripted 60-minute room scenario (enter, sit, leave, return,
   dark, warm) replayed against the learned rule base, scored against the script's ground
   truth. False actuations reported separately from missed ones.
4. **Latency** — reflex-tier decision-to-relay versus cloud round-trip, distributions not
   means.
5. **Offline availability** — network pulled mid-scenario; verify every learned rule still
   fires.
6. **Cloud cost** — NIM tokens and credits consumed across the full corpus.

### 8.3 Baselines

- **B1** — Narada as it exists today. Establishes that none of the 30 requests are currently
  expressible. Expected score: zero. This is the honest floor.
- **B2** — LLM-in-the-loop: every decision is a cloud call. Establishes the latency and cost
  that the proposed design avoids.
- **B3** — proposed.

### 8.4 Threats to validity to state in the paper

Scripted corpus, single room, single camera, authors as speakers, no long-term deployment.
Do not present the suppression rate as a deployment result.

## 9. Hardware

Owned: Pi 5, Hailo-8L HAT, camera, USB mic, 8-channel opto-isolated relay board (7 channels
functional — 3 are needed), DHT22, wiring, XL6009.

To buy, roughly 1,200:

| Item | Approx. | Why |
|---|---|---|
| 12 V LED lamp | 250 | controlled load |
| 12 V DC fan | 250 | second, distinct controlled load |
| 12 V 2 A DC adapter | 300 | dedicated supply for the loads |
| USB speaker | 400 | Pi 5 has no analogue audio out; required for hands-free |

The Pi switches the relay board only; it never supplies load current. Running an ~8 W 12 V
load through the XL6009 off Pi USB draws close to 2 A on the 5 V side and will brown out the
Pi mid-demo.

### 9.1 Echo handling

No hardware echo cancellation on a bare USB mic. Two free mitigations, both required:

- Half-duplex gate: the capture loop stops listening while TTS is playing.
- Spoken responses must never contain the wake word. Current replies at
  `Garuda_web.py:1786` and `:1790` say "Narada" aloud, which would retrigger capture.

## 10. Components and files

| Component | Location | Change |
|---|---|---|
| Scene descriptor builder | new, `basic_pipelines/scene_state.py` | new |
| Rule store + validator | new, `basic_pipelines/rule_store.py` | new |
| Rule evaluator (reflex tier) | new, `basic_pipelines/rule_engine.py` | new |
| Relay driver | new, `basic_pipelines/actuators.py` | new (gpiozero) |
| Local matcher | new, `basic_pipelines/matcher.py` | new |
| NIM synthesis client | `Garuda_web.py:1800` `query_local_llm` | retarget + JSON-constrained rule output |
| Voice loop | `Garuda_web.py:1961` | route unmatched utterances to synthesis; add TTS + half-duplex gate |
| Rule application | `Garuda_web.py:1910` `_apply_llm_result` | replace one-shot flag mutation with rule persistence |

New modules are deliberately small and independently testable. `Garuda_web.py` is already
~3,100 lines; nothing new goes into it that does not have to.

## 11. Out of scope

Cut deliberately, and each is defensible if a reviewer asks:

- **Multi-room, multi-camera.** One camera, one room. Zone policies would be a different paper.
- **mmWave presence sensor.** Redundant with the camera in a single unoccluded room.
- **Lux sensor.** Mean frame luma is a sufficient ambient-brightness proxy and is free.
- **Vision-supervised NILM / energy disaggregation.** Interesting, separate project.
- **Sending any pixels to the cloud.** Boundary is absolute.
- **Rule *editing* by voice.** Create and delete only. Editing multiplies the conflict-check
  surface for no evaluative benefit.
- **Learning rules from observed behaviour** (no utterance). Different contribution entirely.

## 12. Risks

| Risk | Mitigation |
|---|---|
| NIM free credits exhausted mid-evaluation | Instrument token spend from day one; cache synthesis results by utterance hash; the corpus is only 30 requests |
| Model returns plausible but wrong rules | Validator catches malformed; the correct/valid-but-wrong split in §8.2 measures the rest honestly rather than hiding it |
| Paraphrase suppression turns out low | That is still a publishable finding, and the matcher ablation explains it. Do not tune the threshold on the test corpus |
| Pose-derived `posture` unreliable | Descriptor degrades to `none`; rules depending on posture are a reported subset, not a hidden failure |
| Reviewer sees this as an AR933 extension | §3 states the boundary explicitly; B1 demonstrates the current system scores zero |

## 13. Open items

- Confirm whether a USB speaker is already owned.
- Confirm NIM model choice and current free-tier credit allowance at build.nvidia.com.
- Literature check on LLM-based smart-home agents in the first two weeks, before committing.
  Position against rule-synthesis and edge-cloud-split work specifically, not against
  "LLM smart home" generally.

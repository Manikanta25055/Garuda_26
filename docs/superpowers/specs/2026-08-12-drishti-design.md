# Design: Drishti — home automation on the Garuda platform

**Date:** 2026-08-12
**Target:** `drishti.veeramanikanta.in`
**Branch:** `Drishti`
**Builds on:** [Narada-RS design](2026-08-11-narada-rule-synthesis-design.md), which specifies the rule-synthesis architecture and the modules in `basic_pipelines/garuda_auto/`.

---

## 1. What this is

Garuda is a home *security* system: a detection pipeline, alerting, and a web dashboard, running on a Raspberry Pi 5 with a Hailo-8L accelerator. Narada-RS added an automation engine underneath it — spoken instructions compiled into persistent rules, evaluated locally at reflex speed.

Drishti is the product layer over that engine: a home *automation* application with a device registry, an assistant, and a phone-first interface, served from the same backend at a new subdomain.

The Garuda web app is frozen as it stands. Its screenshots appear in the IEEE Access submission and `garuda.veeramanikanta.in` is the live demo cited there; rebuilding it would invalidate both. Drishti is a second frontend against the same FastAPI application.

## 2. Decomposition

Seven sub-projects. This spec covers 1–5; each of the rest gets its own spec.

1. Apple UI/UX principles captured as a reusable skill — **done**, `~/.claude/skills/apple-hig-design/`
2. Device registry and IoT onboarding
3. The assistant: request path, lanes, artifact compilation
4. Information architecture and app shell
5. Changes to existing `garuda_auto` modules
6. Automation UI detail — rule cards, conflict repair, visual builder (deferred)
7. Hosting: DNS, TLS, tunnel (deferred)

## 3. Decisions

| Decision | Choice | Why |
|---|---|---|
| Relationship to Garuda | New frontend, shared backend | Protects the paper's figures and live demo |
| Frontend stack | Vite + Svelte, built on the Mac, `dist/` served by FastAPI | Smallest runtime for a Pi-served connection; no toolchain on the Pi |
| Primary form factor | Phone first, scaling up | Home control happens while moving through the house |
| Chatbot egress | Two lanes; live values never leave the device | Preserves the Narada-RS §7 privacy claim without an asterisk |
| Rule confirmation | Always confirm before saving | The only defence against the *valid-but-wrong* failure class measured in Narada-RS §8.2 |
| IoT scope | GPIO relay channels and MQTT devices (ESP32-C6, ESP32-S3, Uno R4 WiFi) | Covers real add-a-device flows with hardware already owned |
| Device onboarding | Guided, from a fixed device-type catalogue | Derived capabilities cannot contradict what the actuator layer can do |
| Carried from Garuda | Live camera and detections; alerts and modes; users, auth and admin; logs, events and presence | Selected explicitly |

## 4. Architecture

### 4.1 The compiler model, generalised

Narada-RS takes the language model out of the control loop and makes it a compiler: an utterance becomes a persistent rule, validated on-device and evaluated locally thereafter. Drishti keeps that and widens the output to three artifact types.

| Artifact | Shape | Executed |
|---|---|---|
| **Rule** | condition → action | Continuously, by the reflex tier |
| **Task** | named sequence of tool calls, with waits and branches | On demand or on a trigger |
| **Routine** | a set of rules and tasks enabled together under a context | While that context holds |

A Routine is what "autopilot" means concretely: naming a context (Away, Night, Guest) and the set of behaviours that apply while it is active.

This is one compiler with a richer target, not a new subsystem. Validation, storage, conflict detection and the egress boundary are unchanged in kind.

**Only Rules are specified end to end here.** Tasks and Routines require a tool registry — the set of callable operations the compiler may emit, with their parameters and types — and that registry, its schema, and its validation rules are specified with sub-project 6. This spec fixes the architecture they slot into and nothing more. Building the Rule path first is deliberate: it is the path the evaluation measures, and Tasks and Routines reuse its validation, storage and conflict machinery.

### 4.2 Lanes

**Local lane — no network contact.** Questions about live state, explanations of why an action fired, and direct device control. These need current readings, which is exactly why they must never leave the device. Resolved by deterministic patterns over the device registry and the scene descriptor, plus the actuation log. Target under 100 ms; works with the network unplugged.

**Compile lane — cloud, event-triggered.** Reached only when an utterance is not a local answer and is not already covered by a learned rule. Produces an artifact, never a decision.

### 4.3 Egress boundary

Unchanged from Narada-RS §7, and now covering a larger feature surface.

**Crosses to NVIDIA:** the utterance text, the scene-descriptor *schema*, the device registry (identifiers, types, capabilities), the source utterances of existing artifacts, and — once Tasks exist — the tool registry *schema*.

**Never crosses:** frames, crops, keypoints, audio, and any current reading of any descriptor field.

### 4.4 What this architecture cannot do

Open-ended reasoning over live data — "what was unusual about this week" — has no compiled form. It requires a model with the data in front of it, which means either a local model the Pi cannot afford alongside the detection pipeline, or shipping data out. Drishti provides a fixed set of templated local analytics instead. Anything beyond that is out of scope until a local model is worth its memory.

### 4.5 Scope note for the paper

Proactive rule suggestion — noticing a habit and offering a rule — is a weak form of "learning rules from observed behaviour", which Narada-RS §11 cuts as a separate contribution. It ships as a product feature behind a flag, default off, and is excluded from any evaluation run intended for publication.

## 5. Device registry

### 5.1 Two layers

A **device-type catalogue** in code, not user-editable. Each type declares the capabilities a device of that type has and what it contributes to the scene descriptor.

| Type | Actions | Contributes |
|---|---|---|
| `light` | on, off | `<id>_state` enum (on, off) |
| `fan` | on, off | `<id>_state` enum (on, off) |
| `switch` | on, off | `<id>_state` enum (on, off) |
| `sensor.temperature` | — | numeric, −10..60 |
| `sensor.humidity` | — | numeric, 0..100 |
| `sensor.contact` | — | enum (open, closed) |

A **device registry** in `devices.json`, user-authored, where every entry names a catalogue type:

```json
{
  "id": "lamp_desk",
  "name": "Desk lamp",
  "type": "light",
  "room": "study",
  "transport": { "kind": "relay", "channel": 3 },
  "enabled": true
}
```

Onboarding asks four things: type, transport, name, room. Capabilities are derived from the type and never entered by hand. A hand-declared capability that the actuator layer cannot honour would produce rules that pass validation and then fail silently at the relay.

### 5.2 Dynamic schema

`rule_schema.py` gains `build_schema(registry)`, returning fields, devices and operators as an object rather than module constants.

Camera-derived fields — `occupancy`, `person_count`, `occupancy_duration_s`, `zone`, `posture`, `ambient_luma`, `hour` — remain fixed. They come from the detector and are not user-extensible. Device-derived fields are generated: each actuator contributes `<id>_state`, each sensor contributes its typed field.

### 5.3 Transport

`RelayBank` already takes a `pin_map` and needs no change. A `DeviceRouter` above it dispatches by `transport.kind`, with an `MqttBank` exposing the identical `set()` / `state()` interface. `rule_engine.py` is untouched.

MQTT convention: `drishti/<device_id>/set` and `drishti/<device_id>/state`.

### 5.4 Three constraints

**Pin assignment is indirect.** The UI offers relay *channels*; a mapping from channel to BCM pin lives in server config, set once. A user who could enter a pin number could drive a pin the Hailo HAT, camera or I²C bus is using.

**Deleting a device orphans its rules; it does not delete them.** Affected rules are marked `orphaned`, disabled, and surfaced for repair.

**An unreachable device fails loudly.** Relay devices are always in a known state because the system commanded them; an unplugged ESP32 is not. Availability is tracked out of band rather than by adding `unknown` to the state vocabulary — that would let people write rules about reachability, which is a different concept. A rule firing at an unreachable device records a failed actuation.

## 6. Request path

`POST /api/drishti/instruct { text }`, behind `require_session`, with its own rate limit because the compile lane costs money.

1. **Local answer.** State questions, why-did-this-happen, direct control. Returns an answer, not an artifact.
2. **Already known.** `LocalMatcher.match(text)` against stored rules. A hit surfaces the existing rule instead of compiling a duplicate. **Every hit is logged with its score and matcher backend** — this is the paraphrase-suppression event and the headline metric of Narada-RS §8.2. If it is not instrumented here, the number does not exist.
3. **Compile.** Cache lookup by utterance hash first. On miss, NIM synthesis, validation against the current schema, then `find_conflict()`.

### 6.1 Proposals are not rules

Confirm-first requires a stage between synthesis and storage. Pending proposals live in a separate `pending.json`, capped at eight, with a TTL. They are not written to the rule store: storing them disabled would be safe — both `RuleEngine` and `LocalMatcher` skip disabled rules — but they would count against `MAX_RULES` and pollute the file that means "what the house knows".

Confirming a proposal calls `RuleStore.add()`. `RuleEngine` reads `store.rules` live, so the rule is active on the next tick with no reload.

### 6.2 Failure surfaces

| Failure | Presented as |
|---|---|
| Validator rejection | The reason verbatim — "value 900 is outside 0..255 for ambient_luma" |
| Model returns `{"error": ...}` | Its stated reason, plus the vocabulary that does exist |
| Conflict found | Both rules side by side, naming the shared device |
| Network unreachable | Compile failed, **and** an explicit statement that existing rules still fire and lanes 1–2 still work |

The last is a tested guarantee (Narada-RS §8.2 metric 5), so it is asserted rather than shown as a generic error implying the house has stopped.

### 6.3 Resolution marker

Local answers return instantly; compiles take seconds. Each result carries a marker of how it was resolved — on-device, already known, or compiled — so the distinction is visible rather than hidden behind one spinner.

## 7. Information architecture

Four tabs, each answering one question.

| Tab | Question | Contents |
|---|---|---|
| **Home** | What is happening, and what can I touch? | Status card, live view with fullscreen and snapshot/clip, device tiles, mode row |
| **Rules** | What has the house been taught? | Pending proposals, then items needing attention (conflicts, orphans), then rule cards |
| **Activity** | What has it been doing? | One timeline: rule firings with matched conditions, detections, alerts, mode changes, device state changes, presence, logins |
| **Settings** | How is it configured? | Devices & Rooms, People, Alerts, Automation, System |

The composer — "Tell the house what to do" — docks above the tab bar and is reachable from anywhere. It is an action, not a place; making it a tab would reinstate the chat-transcript model this design rejects.

A rule card shows the source utterance as its title, `when` and `then` as chips naming real devices, last fired, fire count, an enable toggle and delete. Navigation is never deeper than two levels from a tab.

### 7.1 Emergency Stop moves

It is currently a navigation item — `{ page: null, label: 'Stop', icon: 'emergency', danger: true }` in `_ADMIN_NAV` — inside the horizontally scrolling row used to switch tabs. A destructive, irreversible control adjacent to navigation targets invites a mis-tap. It moves to Home as a deliberate control with confirmation.

### 7.2 Roles

Four tabs for every user; Settings shows only what the role permits. Garuda swaps the entire nav between `_USER_NAV` and `_ADMIN_NAV`, so the app changes shape depending on who is signed in. A fixed structure with gated contents is simpler and less disorienting.

### 7.3 Left behind

| Dropped from the Drishti UI | Reason |
|---|---|
| Feedback form | Exists for IEEE reviewers, not residents |
| Eval endpoints (`/eval/inject`, `/eval/tag`, `/eval/fps_probe`) | Retained in the backend for the paper; no UI surface |
| Custom voice commands page | Superseded by the rule base; `CUSTOM_VOICE_COMMANDS` is what rules replace |
| Clip exfiltration / SSH upload | Security-specific; stays in the backend |
| Floorplan SVG | Nothing in the automation model uses it |

### 7.4 Empty states and offline

A fresh install has no devices and no rules, so those screens carry the onboarding in sequence: Home asks for a first device, Rules then asks for a first instruction. No separate wizard.

Offline is a state, not an error: a quiet persistent banner stating what still works.

## 8. Changes to existing code

### 8.1 Signature changes

| Site | Change |
|---|---|
| `validator.validate_rule(rule)` | → `validate_rule(rule, schema)`. Breaking; `tests/garuda_auto/test_validator.py` updates with it |
| `scene_state.SceneBuilder(zones)` | Takes the registry; `_device_state` stops being hardcoded to `{"lamp", "fan"}` |
| `nim_client.NimClient.build_request` | Calls `schema.schema_for_prompt()` instead of the module-level function, so new devices reach the synthesis prompt with no code change |
| `rule_store.RuleStore` | Holds a schema reference for `validate_rule` and `_provably_disjoint` |

### 8.2 Two defects to fix

**`RuleStore.load()` silently drops invalid rules.**

```python
self.rules = [r for r in data if validate_rule(r)[0]] if isinstance(data, list) else []
```

Harmless today. Once the schema is built from the device registry, deleting a device invalidates its rules, so the next restart deletes them from disk with no record — the user taught the house something, removed a lamp, rebooted, and the knowledge is gone. `load()` must partition instead: valid rules active, invalid rules retained and marked `orphaned`. This is where §5.4 is actually enforced.

**`NimClient` performs blocking I/O inside an async application.** `requests.post` with a 20-second timeout stalls the event loop, and with it the MJPEG stream, the websocket broadcaster, and every concurrent request. It must run in a threadpool.

## 9. Deployment

Svelte builds on the Mac; `dist/` is rsynced to the Pi and served by the existing FastAPI application under a Drishti-specific route prefix. `garuda_web/` static assets remain mounted and unchanged.

Development happens on the Mac against this repository's `Drishti` branch and is pulled on the Pi. Two things cannot be verified anywhere but the Pi: `gpiozero` relay control, and anything touching Hailo or the camera.

DNS, TLS and tunnel configuration for the new subdomain live on the Pi and are not in this repository. They are sub-project 7.

## 10. Out of scope

- Matter, Zigbee, and Tuya devices — new hardware and a much larger integration surface
- Open-ended reasoning over live data (§4.4)
- Multi-room and multi-camera, inherited from Narada-RS §11
- Rewriting or restyling the Garuda web app
- Voice input to Drishti — the Narada-RS voice loop remains the spoken path; Drishti's composer is typed

## 11. Testing

Existing `tests/garuda_auto/` covers the seven engine modules and continues to. New coverage required:

- `build_schema` over a registry: generated fields, generated devices, and rejection of a type outside the catalogue
- `validate_rule` against a schema built from a registry, including a rule naming a device that has since been deleted
- `RuleStore.load()` partitioning valid from orphaned, asserting no rule is lost across a reload
- The three lanes of `/api/drishti/instruct`, with the compile lane mocked
- Suppression logging: a lane-2 hit records score and backend
- Egress: the request body sent to NIM contains no descriptor value, extending the structural test already written for Narada-RS Task 8
- `DeviceRouter` dispatch across relay and MQTT transports, and failed actuation on an unreachable device

## 12. Open items

- Relay channel to BCM pin mapping for the Pi's 8-channel board — 7 channels functional, 3 currently allocated
- MQTT broker choice and whether it runs on the Pi
- Whether ESP32 firmware announces itself, which would enable auto-discovery in a later revision
- Session and auth reuse: Drishti uses Garuda's existing session cookies, so both subdomains must share a cookie domain or Drishti needs its own login

# Drishti — Apple-grade interface, two shells, clean camera

**Date:** 2026-08-13
**Status:** awaiting review
**Supersedes the interface half of:** `2026-08-12-drishti-design.md`

## Why this exists

The current interface was designed for a phone and then stretched. On a laptop
it is a 390px column indented behind a rail. It also carries a Settings screen
built for the person who wrote it rather than the person who uses it: rule-loop
tick counts, fire counts, orphan counts, and three headings with no
functionality behind them.

Separately, the live camera view is annotated with a debug readout (`Thr: 0.82`)
drawn into the pixels, which also lands in recorded evidence clips and the
WebRTC track.

This spec covers the interface rewrite, the shell split, and the camera clean-up.
It does not change the rule engine, the runtime, or the API contract except where
stated.

## Constraints that shaped it

Measured on the Pi 5 (2026-08-13, 17:52 IST) while the pipeline was running:

| Measure | Value |
| --- | --- |
| Cores | 4 |
| `garuda-web` CPU | 210% |
| Load average | 4.68 |
| SoC temperature | 77.4 °C |
| `vcgencmd get_throttled` | `0x80000` — soft temperature limit tripped |

**There is no CPU headroom.** Any design that adds per-frame work is rejected on
that basis. The camera changes below are net subtractive.

## Section 1 — Feature inventory

### Removed

| What | Where | Why |
| --- | --- | --- |
| Rule-loop telemetry: running / ticks / fires / rules / orphaned / camera state | Settings → System | Developer observability. A resident never asks how many times the loop evaluated. It was added so the *author* could tell the loop was alive. |
| "People", "Alerts", "Automation" sections | Settings (admin) | Three headings, one paragraph each, zero functionality. They make the app look larger than it is and teach the user that headings lead nowhere. |
| `Thr: 0.82` and `PRIVACY ON` text | Burned into the video frame | Debug readout. See Section 3. |
| Nav label "Home" | Tab bar | Apple's own guidance: name a tab for its contents, not a vague umbrella. Becomes **House**. |

Removing the telemetry means the Settings screen no longer reads
`house.state.rule_loop`. The field stays in the API response; nothing in the UI
consumes it. That is deliberate — it costs nothing to leave, and removing it
from `/state` would be a breaking change for no gain.

### Kept, re-presented

- **Occupancy** — one large glanceable line, not a card of three stacked strings.
- **Live camera** — the hero of the House screen, not a box beside a status card.
- **Device tiles** — instant press feedback, busy state, no double-fire.
- **Composer** — teach a rule in a sentence. The core of the product.
- **Rules** — proposals above saved rules; enable, disable, delete; orphans flagged.
- **Activity** — what happened, and the condition that caused it, in plain language.
- **Add / remove device**, **turn everything off**, **sign out**.

### Added

**Privacy control.** `MODE_PRIVACY` already exists in state and `LiveView`
already reads it, but no control anywhere sets it. A camera in a house with no
user-facing off switch is an omission, not a polish item. A toggle over the live
view kills the feed and says so.

This needs a new endpoint. `MODE_PRIVACY` is currently settable only through the
voice assistant (`Garuda_web.py:1756`, `:1758`); there is no HTTP route.
`POST /api/drishti/privacy {"on": bool}` is added, reaching the global through a
`ctx.set_privacy` hook — the same injection pattern already used for
`ctx.system_state`, `ctx.authenticate` and `ctx.frame_source`.

### Explicitly not added

Detection thresholds, model settings, frame rates, pipeline controls, queue
depths. That is the developer surface being removed, and it does not come back
in an admin tab.

## Section 2 — Two shells

Shared state and data layer; two shell components selected by a media-query
store at `768px`. Neither ships the other's CSS, so a phone rule can never
constrain a desktop rule and there are no breakpoint overrides to fight.

```
src/lib/viewport.svelte.js   → reactive `isDesktop`
src/shells/PhoneShell.svelte → floating glass tab bar, bottom
src/shells/DeskShell.svelte  → glass sidebar, left
src/App.svelte               → picks one, renders the active screen into it
```

### Phone

A floating glass capsule detached from the bottom edge, not a full-width strip
welded to the chrome. Safe-area aware via `env(safe-area-inset-bottom)`. It
contracts to icons on scroll-down and returns on scroll-up. The composer docks
directly above it.

### Laptop

A glass sidebar fixed left, with the content scrolling *under* its translucency
rather than beside an opaque panel. Wordmark, full labels, sliding lens. **No
bottom bar exists at any width.** The composer becomes a centred glass pill
floating over the content column.

### Shared rules

- One glass layer only — the navigation. Cards are solid. Never glass on glass.
- The selection lens is one capsule that slides; it animates `transform` only.
- The tablist owns a single tab stop with arrow / Home / End keys.

## Section 3 — Camera

### The finding that made this cheap

The pipeline is:

```
… queue_user_callback ! identity name=identity_callback !
  queue_hailooverlay ! hailooverlay ! videoconvert ! fakesink
```

The buffer probe is attached to `identity_callback`
(`Garuda_web.py:1625`), which sits **upstream of `hailooverlay`**
(`Garuda_web.py:1697`). The frame Python receives therefore has **no detection
boxes in it**. The only annotation is the two `cv2.putText` calls at
`Garuda_web.py:1516`.

A clean stream is a deletion, not a second pipeline branch.

### Changes

1. **Delete both `putText` calls.** `_frame_raw` is assigned from `frame_bgr`
   *after* those calls, and `_frame_raw` feeds the WebRTC track
   (`Garuda_web.py:1012`, `:3596`) and the evidence-clip writer (`:1537`). One
   deletion cleans the Drishti stream, the WebRTC track and saved clips.

2. **Throttle the JPEG encode to 15fps.** The callback currently runs
   `cvtColor(RGB2GRAY)` + `np.var()` for blindness detection, then
   `cvtColor(RGB2BGR)` + `imencode(quality=75)`, on every buffer at pipeline
   rate. No browser needs 60fps MJPEG. Encode at 15fps.

   **Blindness detection keeps its current per-frame cadence.** Its cost is a
   grayscale convert and a variance on one array — small next to a JPEG encode —
   and it counts *consecutive* blind frames, so changing its cadence silently
   changes how long the camera must be covered before it alerts. That is a
   behaviour change disguised as an optimisation, and it is not worth bundling
   with a rendering fix.

   **This also fixes an existing bug.** `cv2.VideoWriter` is constructed with a
   hardcoded `15.0` fps (`Garuda_web.py:3662`) but `_clip_writer.write()` is
   called from the callback at full pipeline rate (`:1537`). Every recorded
   evidence clip is therefore written with several times more frames than its
   header declares, and plays back in slow motion. Gating the clip write on the
   same 15fps throttle as the encode makes the file's real rate match its
   declared rate. **The clip write must share the throttle, not sit outside it**
   — leaving it at full rate preserves the bug.

3. **Drop the discarded overlay tail.** `hailooverlay` and the `videoconvert`
   after it render into `fakesink`; nothing consumes the result. Terminating the
   branch earlier returns CPU on a thermally-throttled board.

Steps 1 and 2 are safe and independent. **Step 3 is a pipeline change on a
system that crash-looped today** (libcamera `stl_queue` assertion, 7 restarts in
5 minutes) and must ship separately, with load and temperature measured before
and after, and a one-command revert.

### Frontend

Fixed-aspect rounded container, `object-fit: cover`, so a 16:9 sensor never
letterboxes inside a box the layout sized. Full-bleed on phone; hero at capped
width on laptop. Privacy toggle overlays it. A `<figure>` wrapper with the image
in a positioned stack, so a future detection overlay can be added as absolutely
positioned SVG without touching layout.

### Forward compatibility

If boxes are wanted later they are drawn client-side from detection geometry —
crisp at any scale, toggleable, styleable, and free of per-frame CPU on the Pi.
That needs bounding boxes exposed on an endpoint, which this spec does **not**
build. It only ensures the live view's DOM makes it a drop-in.

## Section 4 — The Apple system

**Type.** Tracking is size-specific: display text at `-0.02em`, body at `0`.
Leading tight on large text, looser on body. Sizes in `rem` so a 200% text
setting scales the layout instead of breaking it. System font stack.

**Motion.** Springs, not CSS transitions, for anything a user can touch.
Default `damping 1.0`, `response 0.35` — critically damped, no overshoot. Bounce
(`damping 0.8`) only after a gesture that carried momentum. Feedback on
pointer-*down*, never on release. Animations start from the current on-screen
value so an interrupted transition never jumps.

**Material.** `backdrop-filter: blur() saturate()` on floating chrome — the
navigation and the composer. The rule is **never glass over glass**: two
translucent surfaces may both exist, but must never overlap, because stacking
them collapses legibility. The sidebar and the composer occupy different regions
and never intersect, so both are glass. Cards, sheets and tiles are solid.
Bright top edge on the glass to read as light catching a surface. Content
scrolls under the chrome rather than beside it.

**Accessibility.** `prefers-reduced-motion` → cross-fade, no spring, no
transform. `prefers-reduced-transparency` → solid surface, no blur.
`prefers-contrast: more` → near-solid background with a defined border. All
three already partially present; this makes them uniform.

## Testing

- Existing 149 frontend tests must keep passing; they are written against
  behaviour (roles, labels, states), not markup, so the shell split should not
  invalidate them. Any that break because they assert a phone-only structure get
  rewritten against the shell they actually target.
- **Known, bounded rework:** `tests/polish.test.js` carries 5 assertions against
  the rule-loop telemetry being removed in Section 1. Those are deleted with the
  feature, not rewritten — the behaviour they cover is going away on purpose.
  No other test file references it.
- New: the viewport store picks exactly one shell, and the two shells never both
  mount.
- New: the privacy toggle stops the feed and is announced.
- Camera changes verified by measurement, not assertion: load average and
  `vcgencmd measure_temp` before and after, recorded in the commit.

## Sequencing

Split into two passes, decided 2026-08-13. Pass one exists so the direction can
be judged on a real screen before four more are rewritten to match it.

**Pass one — this plan**

1. Camera steps 1 and 2 — independently verifiable, returns CPU, fixes the clip
   framerate bug.
2. Viewport store and the two shells — no screen redesign yet, existing screens
   render inside whichever shell is chosen.
3. House screen: occupancy headline, live view as hero, privacy toggle, device
   tiles.

**Pass two — a later plan**

4. Rules, Activity, Settings, Login redesigned to match.
5. Settings feature cut (Section 1 removals) and the `polish.test.js` deletions.
6. Camera step 3, the `hailooverlay` tail — measured before and after,
   separately revertible, kept away from UI work because this file crash-looped
   seven times on 2026-08-13.

## Closed items

**Pipeline topology.** Established from source and confirmed visually on
2026-08-13: no detection boxes are visible on the live view. The probe is
upstream of `hailooverlay` as read. Section 3 stands.

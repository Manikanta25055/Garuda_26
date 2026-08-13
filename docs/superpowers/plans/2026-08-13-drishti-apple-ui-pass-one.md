# Drishti Apple UI — Pass One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a clean camera feed, a privacy switch, and two genuinely separate phone/laptop shells with the House screen redesigned, so the direction can be judged before four more screens are rewritten to match.

**Architecture:** The camera work moves the encode out of `Garuda_web.py`'s callback into a small testable `FramePublisher` that gates encoding to 15fps and never draws on the frame. The privacy switch reaches `MODE_PRIVACY` through a `ctx.set_privacy` hook, the same injection pattern already used for `ctx.system_state`. On the frontend a `viewport` store picks exactly one of two shell components; neither ships the other's CSS, so there are no breakpoint overrides to fight.

**Tech Stack:** Python 3.11, FastAPI, OpenCV, pytest. Svelte 5 (runes), Vite 6, Vitest 2, @testing-library/svelte, jsdom.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-13-drishti-apple-ui-design.md`. Read it before Task 1.
- **No new per-frame CPU.** The Pi measured 210% CPU across 4 cores, load 4.68, 77.4 °C, `vcgencmd get_throttled` = `0x80000` (soft temperature limit tripped). Any change that adds per-frame work is wrong.
- **Nothing is drawn into the frame.** `_frame_raw` feeds the MJPEG stream, the WebRTC track and the evidence-clip writer. Annotation added there reaches all three.
- **The clip write shares the encode gate.** `cv2.VideoWriter` is built at a hardcoded `15.0` fps (`Garuda_web.py:3662`); writing faster than that is the existing slow-motion bug.
- **Blindness detection keeps its per-frame cadence.** It counts *consecutive* blind frames; changing its rate changes how long the camera must be covered before it alerts.
- **gpiozero only** for GPIO. Never RPi.GPIO.
- **Design tokens come from `src/styles/tokens.css`.** Never hardcode a colour, size, radius or duration in a component.
- **Hit areas are never below 44px.**
- **Run the Python suite from `~/Projects/Garuda_26` on the Pi**, not from a worktree — `load_dotenv()` resolves relative to the calling file.
- **Baseline is green:** 594 Python tests, 149 frontend tests, zero failures. Any failure is new.

---

### Task 1: FramePublisher — gate the encode, never annotate

**Files:**
- Create: `basic_pipelines/garuda_auto/frame_publisher.py`
- Test: `tests/garuda_auto/test_frame_publisher.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FramePublisher(fps=15.0, clock=time.monotonic)` with `due() -> bool` and static `encode(frame_rgb, quality=75) -> tuple[numpy.ndarray, bytes]` returning `(bgr_frame, jpeg_bytes)`. Task 2 consumes both.

- [ ] **Step 1: Write the failing test**

```python
# tests/garuda_auto/test_frame_publisher.py
import numpy as np
import pytest

from basic_pipelines.garuda_auto.frame_publisher import FramePublisher


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _frame():
    """A frame whose channels differ, so a channel swap is detectable."""
    f = np.zeros((8, 8, 3), dtype=np.uint8)
    f[:, :, 0] = 10    # R
    f[:, :, 1] = 20    # G
    f[:, :, 2] = 30    # B
    return f


def test_first_frame_is_always_due():
    assert FramePublisher(fps=15.0, clock=FakeClock()).due() is True


def test_a_second_frame_in_the_same_instant_is_not_due():
    p = FramePublisher(fps=15.0, clock=FakeClock())
    assert p.due() is True
    assert p.due() is False


def test_due_again_once_the_interval_has_passed():
    clock = FakeClock()
    p = FramePublisher(fps=15.0, clock=clock)
    assert p.due() is True
    clock.t = 1 / 15 - 0.001
    assert p.due() is False
    clock.t = 1 / 15
    assert p.due() is True


def test_sixty_calls_over_one_second_yield_fifteen_publishes():
    """The whole point: pipeline rate in, browser rate out."""
    clock = FakeClock()
    p = FramePublisher(fps=15.0, clock=clock)
    published = 0
    for i in range(60):
        clock.t = i / 60
        if p.due():
            published += 1
    assert published == 15


def test_encode_does_not_modify_the_frame_it_was_given():
    """The frame is shared with the WebRTC track and the clip writer."""
    frame = _frame()
    before = frame.copy()
    FramePublisher.encode(frame)
    assert np.array_equal(frame, before)


def test_encode_returns_bgr():
    bgr, _ = FramePublisher.encode(_frame())
    assert bgr[0, 0, 0] == 30    # B where R was
    assert bgr[0, 0, 1] == 20
    assert bgr[0, 0, 2] == 10


def test_encode_returns_jpeg_bytes():
    _, jpeg = FramePublisher.encode(_frame())
    assert isinstance(jpeg, bytes)
    assert jpeg.startswith(b"\xff\xd8")    # SOI marker
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/Garuda_26 && python3 -m pytest tests/garuda_auto/test_frame_publisher.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'basic_pipelines.garuda_auto.frame_publisher'`

- [ ] **Step 3: Write minimal implementation**

```python
# basic_pipelines/garuda_auto/frame_publisher.py
"""Encode a camera frame for the browser, at a rate the browser can use.

The GStreamer callback runs at pipeline rate, and a JPEG encode at that rate is
the most expensive thing in it. No browser can use 60fps MJPEG, so the encode is
gated to a fixed interval.

The clip writer shares that gate. cv2.VideoWriter is constructed at a hardcoded
15fps, and writing to it faster than that is what makes saved clips play back in
slow motion.

Nothing is drawn on the frame here. The debug readout that used to be drawn at
this point also reached the WebRTC track and every saved evidence clip, because
all three read the same array.
"""
import time

import cv2

PUBLISH_FPS = 15.0


class FramePublisher:
    def __init__(self, fps=PUBLISH_FPS, clock=time.monotonic):
        self._interval = 1.0 / fps
        self._clock = clock
        self._last = None

    def due(self):
        """True at most `fps` times a second, and always on the first call."""
        now = self._clock()
        if self._last is not None and (now - self._last) < self._interval:
            return False
        self._last = now
        return True

    @staticmethod
    def encode(frame_rgb, quality=75):
        """Return (bgr_frame, jpeg_bytes). The frame passed in is never modified."""
        bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise ValueError("JPEG encode failed")
        return bgr, buf.tobytes()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Projects/Garuda_26 && python3 -m pytest tests/garuda_auto/test_frame_publisher.py`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/garuda_auto/frame_publisher.py tests/garuda_auto/test_frame_publisher.py
git commit -m "feat(camera): frame publisher that gates the encode and never annotates"
```

---

### Task 2: Wire the publisher into the callback

**Files:**
- Modify: `basic_pipelines/Garuda_web.py:1515-1545`
- Modify: `basic_pipelines/Garuda_web.py` — module-level publisher near `_frame_lock` (declared around `:426`)

**Interfaces:**
- Consumes: `FramePublisher` from Task 1.
- Produces: no new callable. `_frame_buffer`, `_frame_raw`, `_frame_seq` and `_frame_ts` keep their current meanings and update at 15fps instead of pipeline rate.

**Context the implementer needs.** The current block is:

```python
    if frame is not None:
        cv2.putText(frame, f"Thr: {threshold:.2f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        if privacy:
            cv2.putText(frame, "PRIVACY ON", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 80, 80), 2)
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        _, jpeg = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
        with _frame_lock:
            global _frame_seq, _frame_raw, _frame_ts
            _frame_buffer = jpeg.tobytes()
            _frame_raw    = frame_bgr
            _frame_seq += 1
            _frame_ts     = time.time()
        user_data.set_frame(frame_bgr)
```

immediately followed by the clip-recording block that calls `_clip_writer.write(frame_bgr)`.

- [ ] **Step 1: Add the module-level publisher**

Next to the other frame globals (near `_frame_raw` at `:426`):

```python
# Pipeline rate in, browser rate out. The clip writer shares this gate because
# its VideoWriter is fixed at 15fps.
from .garuda_auto.frame_publisher import FramePublisher   # noqa: E402
_frame_publisher = FramePublisher()
```

If that relative import fails because the module is run as a script, mirror the
existing dual-import pattern used at `:182-186`:

```python
try:
    from .garuda_auto.frame_publisher import FramePublisher
except ImportError:
    from basic_pipelines.garuda_auto.frame_publisher import FramePublisher
```

- [ ] **Step 2: Replace the block**

```python
    # Gated: the encode is the most expensive thing in this callback, and no
    # browser can use 60fps MJPEG. Nothing is drawn on the frame — the debug
    # readout that used to be here also reached the WebRTC track and every
    # saved evidence clip, which all read _frame_raw.
    if frame is not None and _frame_publisher.due():
        frame_bgr, jpeg = FramePublisher.encode(frame)
        with _frame_lock:
            global _frame_seq, _frame_raw, _frame_ts
            _frame_buffer = jpeg
            _frame_raw    = frame_bgr
            _frame_seq += 1
            _frame_ts     = time.time()
        user_data.set_frame(frame_bgr)
```

- [ ] **Step 3: Move the clip write inside the same gate**

The clip block must become part of the `if` above — indented into it, using the
same `frame_bgr`. It currently sits outside and runs at pipeline rate, which is
the slow-motion bug. Keep its lock and its 60-second auto-stop exactly as they
are; only its cadence changes.

- [ ] **Step 4: Verify nothing references the removed names**

Run: `cd ~/Projects/Garuda_26 && grep -n "Thr: \|PRIVACY ON" basic_pipelines/Garuda_web.py`
Expected: no output.

Run: `cd ~/Projects/Garuda_26 && python3 -c "import ast,sys; ast.parse(open('basic_pipelines/Garuda_web.py').read())"`
Expected: no output (parses).

- [ ] **Step 5: Run the full Python suite**

Run: `cd ~/Projects/Garuda_26 && python3 -m pytest`
Expected: `601 passed` (594 baseline + 7 from Task 1), zero failures.

- [ ] **Step 6: Restart and measure**

```bash
sudo systemctl restart garuda-web.service
sleep 90
cat /proc/loadavg; vcgencmd measure_temp; systemctl is-active garuda-web.service
```

Expected: service `active`; load average and temperature **at or below** the
77.4 °C / 4.68 baseline. If either is higher, the gate is not being hit — stop
and diagnose before continuing.

- [ ] **Step 7: Commit**

```bash
git add basic_pipelines/Garuda_web.py
git commit -m "fix(camera): stop annotating the frame, gate encode and clip writes to 15fps

The debug readout reached the MJPEG stream, the WebRTC track and every saved
evidence clip, because all three read _frame_raw. The clip writer also ran at
pipeline rate against a VideoWriter fixed at 15fps, so clips played back in
slow motion; it now shares the gate."
```

---

### Task 3: Privacy endpoint

**Files:**
- Modify: `basic_pipelines/drishti_api.py` — `DrishtiContext` (add `set_privacy`), and a new route beside `/state`
- Test: `tests/test_drishti_api.py` (append)

**Interfaces:**
- Consumes: `ctx.system_state` convention from the existing code.
- Produces: `ctx.set_privacy: object = None`, a callable `(bool) -> None`. `POST /api/drishti/privacy` with body `{"on": bool}` returning `{"privacy": bool}`. Task 4 supplies the callable; Task 9 calls the route.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_drishti_api.py

def test_privacy_can_be_turned_on(client):
    test_client, ctx = client
    seen = []
    ctx.set_privacy = lambda on: seen.append(on)
    response = test_client.post("/privacy", json={"on": True})
    assert response.status_code == 200
    assert response.json() == {"privacy": True}
    assert seen == [True]


def test_privacy_can_be_turned_off(client):
    test_client, ctx = client
    seen = []
    ctx.set_privacy = lambda on: seen.append(on)
    assert test_client.post("/privacy", json={"on": False}).json() == {"privacy": False}
    assert seen == [False]


def test_privacy_needs_a_session(anonymous):
    """A camera switch an anonymous caller can throw is not a privacy control."""
    assert anonymous.post("/privacy", json={"on": True}).status_code == 401


def test_privacy_reports_when_no_camera_is_wired(client):
    test_client, ctx = client
    ctx.set_privacy = None
    assert test_client.post("/privacy", json={"on": True}).status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/Garuda_26 && python3 -m pytest tests/test_drishti_api.py -k privacy`
Expected: FAIL — 404 on `/privacy`

- [ ] **Step 3: Add the context field**

In `DrishtiContext`, beside the other injected hooks:

```python
    set_privacy: object = None     # (bool) -> None, flips the camera off
```

- [ ] **Step 4: Add the request model and the route**

Beside the other request models:

```python
class PrivacyRequest(BaseModel):
    on: bool
```

Beside `/state`:

```python
    @router.post("/privacy")
    async def privacy(body: PrivacyRequest, session=Depends(require_drishti_session)):
        # Authenticated first, so an anonymous caller gets 401 rather than 503
        # and cannot use this to probe whether a camera exists.
        if ctx.set_privacy is None:
            raise HTTPException(status_code=503, detail="no camera on this host")
        ctx.set_privacy(body.on)
        return {"privacy": body.on}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/Projects/Garuda_26 && python3 -m pytest tests/test_drishti_api.py -k privacy`
Expected: PASS, 4 passed

- [ ] **Step 6: Commit**

```bash
git add basic_pipelines/drishti_api.py tests/test_drishti_api.py
git commit -m "feat(api): privacy route so the camera has a user-facing off switch"
```

---

### Task 4: Wire the privacy hook to MODE_PRIVACY

**Files:**
- Modify: `basic_pipelines/Garuda_web.py` — beside `DRISHTI_CTX.system_state = _drishti_system_state` (around `:2472`)

**Interfaces:**
- Consumes: `ctx.set_privacy` from Task 3.
- Produces: nothing new.

- [ ] **Step 1: Add the setter**

Beside the existing `_drishti_system_state` wiring:

```python
def _drishti_set_privacy(on):
    """Flip the camera off from the app.

    MODE_PRIVACY was reachable only through the voice assistant, so the web app
    could read the flag and never change it.
    """
    global MODE_PRIVACY
    MODE_PRIVACY = bool(on)
    log_system_update(f"[DRISHTI] privacy {'on' if MODE_PRIVACY else 'off'}")


DRISHTI_CTX.set_privacy = _drishti_set_privacy
```

- [ ] **Step 2: Verify it parses and the suite is green**

Run: `cd ~/Projects/Garuda_26 && python3 -m pytest`
Expected: `605 passed`, zero failures.

- [ ] **Step 3: Commit**

```bash
git add basic_pipelines/Garuda_web.py
git commit -m "feat(camera): let the app turn privacy mode on and off"
```

---

### Task 5: Viewport store

**Files:**
- Create: `drishti_web/src/lib/viewport.svelte.js`
- Test: `drishti_web/tests/viewport.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: `Viewport` class taking an optional `matchMedia` function; instance property `isDesktop` (reactive boolean). Default export instance `viewport`. Constant `DESKTOP_QUERY = "(min-width: 768px)"`. Tasks 6–8 consume `viewport.isDesktop`.

All frontend commands run from `drishti_web/` with `export PATH=$HOME/.local/bin:$PATH` (npm is a corepack shim there).

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/viewport.test.js
import { describe, it, expect } from "vitest";
import { Viewport, DESKTOP_QUERY } from "../src/lib/viewport.svelte.js";

function fakeMatchMedia(matches) {
  const listeners = [];
  const mql = {
    matches,
    media: DESKTOP_QUERY,
    addEventListener: (_, fn) => listeners.push(fn),
    removeEventListener: () => {},
  };
  const fn = (query) => {
    fn.lastQuery = query;
    return mql;
  };
  fn.fire = (next) => {
    mql.matches = next;
    listeners.forEach((l) => l({ matches: next }));
  };
  return fn;
}

describe("viewport", () => {
  it("asks about the 768px line the tokens already change on", () => {
    const mm = fakeMatchMedia(false);
    new Viewport(mm);
    expect(mm.lastQuery).toBe("(min-width: 768px)");
  });

  it("starts on the phone shell below the line", () => {
    expect(new Viewport(fakeMatchMedia(false)).isDesktop).toBe(false);
  });

  it("starts on the desktop shell above the line", () => {
    expect(new Viewport(fakeMatchMedia(true)).isDesktop).toBe(true);
  });

  it("follows the window across the line", () => {
    const mm = fakeMatchMedia(false);
    const v = new Viewport(mm);
    mm.fire(true);
    expect(v.isDesktop).toBe(true);
    mm.fire(false);
    expect(v.isDesktop).toBe(false);
  });

  it("stays on the phone shell where matchMedia does not exist", () => {
    // Not decoration: jsdom has historically shipped without it, and a throw
    // here would take the whole app down before anything rendered.
    expect(new Viewport(undefined).isDesktop).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npx vitest run tests/viewport.test.js`
Expected: FAIL — cannot resolve `../src/lib/viewport.svelte.js`

- [ ] **Step 3: Write minimal implementation**

```js
// drishti_web/src/lib/viewport.svelte.js
/**
 * Which shell is mounted.
 *
 * 768px is the line the content margin and the type scale already change on, so
 * the shells change there too rather than introducing a second breakpoint.
 *
 * This is a store rather than a CSS breakpoint because the two shells are
 * separate components: only one is ever in the DOM. A phone rule therefore
 * cannot reach a desktop element, and there is nothing to override.
 */
export const DESKTOP_QUERY = "(min-width: 768px)";

export class Viewport {
  isDesktop = $state(false);

  constructor(matchMedia = globalThis.matchMedia) {
    if (typeof matchMedia !== "function") return;
    const mql = matchMedia(DESKTOP_QUERY);
    this.isDesktop = mql.matches;
    mql.addEventListener("change", (event) => (this.isDesktop = event.matches));
  }
}

export const viewport = new Viewport();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npx vitest run tests/viewport.test.js`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/lib/viewport.svelte.js drishti_web/tests/viewport.test.js
git commit -m "feat(web): viewport store that picks exactly one shell"
```

---

### Task 6: PhoneShell — floating glass capsule

**Files:**
- Create: `drishti_web/src/shells/PhoneShell.svelte`
- Test: `drishti_web/tests/phone-shell.test.js`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `PhoneShell` taking props `current` (string tab id), `onchange` (function), and a `children` snippet. Renders `role="tablist"` with four tabs: `house`, `rules`, `activity`, `settings`, labelled `House`, `Rules`, `Activity`, `Settings`. Task 8 mounts it.

Note the tab id and label change: `home` → `house`, `"Home"` → `"House"`.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/phone-shell.test.js
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import PhoneShell from "../src/shells/PhoneShell.svelte";

const props = (over = {}) => ({ current: "house", onchange: () => {}, ...over });

describe("phone shell", () => {
  it("names the first tab for its contents, not as an umbrella", () => {
    render(PhoneShell, props());
    expect(screen.getAllByRole("tab").map((t) => t.textContent.trim()))
      .toEqual(["House", "Rules", "Activity", "Settings"]);
  });

  it("reports a tab change by id", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ onchange }));
    await fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(onchange).toHaveBeenCalledWith("activity");
  });

  it("keeps one tab stop and moves within it with the arrows", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ current: "house", onchange }));
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1", "-1"]);
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onchange).toHaveBeenCalledWith("rules");
  });

  it("wraps End to the last tab", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ onchange }));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    expect(onchange).toHaveBeenCalledWith("settings");
  });

  it("floats clear of the bottom edge and respects the safe area", () => {
    // Asserted against the source: jsdom evaluates neither env() nor the
    // media query, and the claim is about the stylesheet.
    const src = readFileSync(resolve("src/shells/PhoneShell.svelte"), "utf8");
    expect(src).toMatch(/env\(safe-area-inset-bottom\)/);
    expect(src).toMatch(/backdrop-filter/);
  });

  it("carries no sidebar rule at any width", () => {
    const src = readFileSync(resolve("src/shells/PhoneShell.svelte"), "utf8");
    expect(src).not.toMatch(/min-width:\s*768px/);
  });

  it("gives every tab a 44px hit area", () => {
    const src = readFileSync(resolve("src/shells/PhoneShell.svelte"), "utf8");
    expect(src).toMatch(/min-height:\s*44px/);
  });

  it("goes solid when the user asks for less transparency", () => {
    const src = readFileSync(resolve("src/shells/PhoneShell.svelte"), "utf8");
    expect(src).toMatch(/prefers-reduced-transparency/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npx vitest run tests/phone-shell.test.js`
Expected: FAIL — cannot resolve `../src/shells/PhoneShell.svelte`

- [ ] **Step 3: Write minimal implementation**

```svelte
<!-- drishti_web/src/shells/PhoneShell.svelte -->
<script>
  let { current, onchange, children } = $props();

  // Named for their contents. "Home" was an umbrella that told you nothing
  // about what was behind it.
  const TABS = [
    { id: "house", label: "House",
      d: ["M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z"] },
    { id: "rules", label: "Rules", d: ["M4 6h16M4 12h16M4 18h10"] },
    { id: "activity", label: "Activity", d: ["M3 12h4l3 8 4-16 3 8h4"] },
    { id: "settings", label: "Settings",
      d: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
          "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"] },
  ];

  const index = $derived(Math.max(0, TABS.findIndex((t) => t.id === current)));

  function onkeydown(event) {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (step) {
      event.preventDefault();
      onchange(TABS[(index + step + TABS.length) % TABS.length].id);
    } else if (event.key === "Home") {
      event.preventDefault();
      onchange(TABS[0].id);
    } else if (event.key === "End") {
      event.preventDefault();
      onchange(TABS[TABS.length - 1].id);
    }
  }
</script>

<main id="panel" role="tabpanel" aria-labelledby="tab-{current}" tabindex="-1">
  {@render children()}
</main>

<nav class="bar" aria-label="Sections" style="--count: {TABS.length}; --active: {index}">
  <div class="tabs" role="tablist" {onkeydown}>
    <span class="lens" aria-hidden="true"></span>
    {#each TABS as tab, i}
      <button
        role="tab"
        id="tab-{tab.id}"
        aria-selected={current === tab.id}
        aria-controls="panel"
        tabindex={i === index ? 0 : -1}
        onclick={() => onchange(tab.id)}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          {#each tab.d as path}<path d={path} />{/each}
        </svg>
        <span>{tab.label}</span>
      </button>
    {/each}
  </div>
</nav>

<style>
  main {
    padding: max(var(--space-4), env(safe-area-inset-top)) var(--margin-content) 0;
    /* Clears the composer and the floating bar beneath it. */
    padding-bottom: calc(170px + env(safe-area-inset-bottom));
  }
  main:focus { outline: none; }

  /* A capsule that floats clear of the edge, rather than a strip welded to the
     chrome. The inset is what makes it read as an object above the content. */
  .bar {
    position: fixed;
    z-index: 20;
    inset: auto var(--space-3) calc(var(--space-3) + env(safe-area-inset-bottom)) var(--space-3);
    padding: var(--space-1);
    border-radius: 9999px;
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    box-shadow: 0 8px 32px rgb(0 0 0 / 0.16);
    /* The bright top edge reads as light catching a real material. */
    border-top: 0.5px solid rgb(255 255 255 / 0.4);
  }

  .tabs {
    position: relative;
    display: grid;
    grid-template-columns: repeat(var(--count), 1fr);
  }

  button {
    position: relative;
    z-index: 1;
    display: grid;
    justify-items: center;
    gap: 2px;
    min-height: 44px;
    padding: var(--space-1) 0;
    color: var(--label-secondary);
    transition: color var(--dur-base) var(--ease-standard);
  }
  button[aria-selected="true"] { color: var(--accent); }
  /* Weight as well as colour, so the selection survives colour-vision
     deficiency. */
  button[aria-selected="true"] span { font-weight: var(--weight-semibold); }

  svg { width: 24px; height: 24px; }
  span { font-size: var(--text-caption-2); line-height: var(--lh-caption-2); font-weight: var(--weight-medium); }

  /* One capsule that slides, not four states appearing and disappearing.
     transform only, so a tab change never relayouts the bar. */
  .lens {
    position: absolute;
    z-index: 0;
    inset: 0 auto 0 0;
    width: calc(100% / var(--count));
    border-radius: 9999px;
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    transform: translateX(calc(var(--active) * 100%));
    transition: transform var(--dur-base) var(--ease-spring);
    pointer-events: none;
  }

  @media (prefers-reduced-motion: reduce) {
    .lens { transition: none; }
  }
  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .bar {
      background: var(--surface);
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
      border: 0.5px solid var(--separator);
    }
  }
</style>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npx vitest run tests/phone-shell.test.js`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/shells/PhoneShell.svelte drishti_web/tests/phone-shell.test.js
git commit -m "feat(web): phone shell with a floating glass tab bar"
```

---

### Task 7: DeskShell — glass sidebar

**Files:**
- Create: `drishti_web/src/shells/DeskShell.svelte`
- Test: `drishti_web/tests/desk-shell.test.js`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `DeskShell` with the same props as `PhoneShell` — `current`, `onchange`, `children` — and the same four tab ids and labels, so Task 8 can swap them without a conditional on props.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/desk-shell.test.js
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import DeskShell from "../src/shells/DeskShell.svelte";

const props = (over = {}) => ({ current: "house", onchange: () => {}, ...over });

describe("desk shell", () => {
  it("offers the same four destinations as the phone", () => {
    render(DeskShell, props());
    expect(screen.getAllByRole("tab").map((t) => t.textContent.trim()))
      .toEqual(["House", "Rules", "Activity", "Settings"]);
  });

  it("shows the wordmark the phone has no room for", () => {
    render(DeskShell, props());
    expect(screen.getByText("Drishti")).toBeInTheDocument();
  });

  it("reports a tab change by id", async () => {
    const onchange = vi.fn();
    render(DeskShell, props({ onchange }));
    await fireEvent.click(screen.getByRole("tab", { name: "Rules" }));
    expect(onchange).toHaveBeenCalledWith("rules");
  });

  it("moves with the arrow keys from a single tab stop", async () => {
    const onchange = vi.fn();
    render(DeskShell, props({ current: "house", onchange }));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowDown" });
    expect(onchange).toHaveBeenCalledWith("rules");
  });

  it("carries no bottom-bar rule at any width", () => {
    // The two shells must not overlap. A bottom inset here would mean the
    // phone design had leaked into the laptop one.
    const src = readFileSync(resolve("src/shells/DeskShell.svelte"), "utf8");
    expect(src).not.toMatch(/max-width:\s*767/);
    expect(src).not.toMatch(/inset:\s*auto 0 0 0/);
  });

  it("caps the content column so text does not run to a hundred characters", () => {
    const src = readFileSync(resolve("src/shells/DeskShell.svelte"), "utf8");
    expect(src).toMatch(/max-width:\s*var\(--measure\)/);
  });

  it("goes solid when the user asks for less transparency", () => {
    const src = readFileSync(resolve("src/shells/DeskShell.svelte"), "utf8");
    expect(src).toMatch(/prefers-reduced-transparency/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npx vitest run tests/desk-shell.test.js`
Expected: FAIL — cannot resolve `../src/shells/DeskShell.svelte`

- [ ] **Step 3: Add the measure token**

In `src/styles/tokens.css`, in the spacing block:

```css
  --measure: 56rem;         /* content column cap; ~75 characters at body size */
  --rail: 15rem;            /* desktop sidebar width */
```

- [ ] **Step 4: Write minimal implementation**

Same `TABS` array and same `onkeydown` as `PhoneShell` (repeat it — the two
shells are deliberately independent, and sharing it would reintroduce the
coupling this split exists to remove), with `ArrowDown`/`ArrowUp` mapped to
`+1`/`-1` instead of `ArrowRight`/`ArrowLeft`, and this markup and style:

```svelte
<nav class="rail" aria-label="Sections" style="--active: {index}">
  <p class="wordmark">Drishti</p>
  <div class="tabs" role="tablist" {onkeydown}>
    <span class="lens" aria-hidden="true"></span>
    {#each TABS as tab, i}
      <button
        role="tab"
        id="tab-{tab.id}"
        aria-selected={current === tab.id}
        aria-controls="panel"
        tabindex={i === index ? 0 : -1}
        onclick={() => onchange(tab.id)}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          {#each tab.d as path}<path d={path} />{/each}
        </svg>
        <span>{tab.label}</span>
      </button>
    {/each}
  </div>
</nav>

<main id="panel" role="tabpanel" aria-labelledby="tab-{current}" tabindex="-1">
  {@render children()}
</main>

<style>
  /* Content scrolls under the rail's translucency rather than beside an opaque
     panel, which is what makes the material read as glass rather than paint. */
  .rail {
    --tab-h: 44px;
    position: fixed;
    z-index: 20;
    inset: 0 auto 0 0;
    width: var(--rail);
    padding: var(--space-8) var(--space-3) var(--space-4);
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border-right: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
  }

  .wordmark {
    margin: 0 0 var(--space-5) var(--space-3);
    font-size: var(--text-title-3);
    line-height: var(--lh-title-3);
    font-weight: var(--weight-bold);
    letter-spacing: -0.01em;
  }

  .tabs { position: relative; display: grid; grid-auto-rows: var(--tab-h); gap: var(--space-1); }

  button {
    position: relative;
    z-index: 1;
    display: grid;
    grid-auto-flow: column;
    justify-content: start;
    align-items: center;
    gap: var(--space-3);
    padding: 0 var(--space-3);
    border-radius: var(--radius-control);
    color: var(--label-secondary);
    transition: color var(--dur-base) var(--ease-standard);
  }
  button[aria-selected="true"] { color: var(--accent); }
  button[aria-selected="true"] span { font-weight: var(--weight-semibold); }

  svg { width: 22px; height: 22px; }
  span { font-size: var(--text-callout); font-weight: var(--weight-medium); }

  .lens {
    position: absolute;
    z-index: 0;
    inset: 0 0 auto 0;
    height: var(--tab-h);
    border-radius: var(--radius-control);
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    /* The gap belongs in the step, or the lens drifts off its label. */
    transform: translateY(calc(var(--active) * (var(--tab-h) + var(--space-1))));
    transition: transform var(--dur-base) var(--ease-spring);
    pointer-events: none;
  }

  main {
    margin-left: var(--rail);
    max-width: var(--measure);
    padding: var(--space-8) var(--margin-content) calc(140px + var(--space-4));
    margin-right: auto;
    padding-left: max(var(--margin-content), var(--space-8));
  }
  main:focus { outline: none; }

  @media (prefers-reduced-motion: reduce) {
    .lens { transition: none; }
  }
  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .rail {
      background: var(--surface);
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
    }
  }
</style>
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd drishti_web && npx vitest run tests/desk-shell.test.js`
Expected: PASS, 7 passed

- [ ] **Step 6: Commit**

```bash
git add drishti_web/src/shells/DeskShell.svelte drishti_web/src/styles/tokens.css drishti_web/tests/desk-shell.test.js
git commit -m "feat(web): desktop shell with a glass sidebar"
```

---

### Task 8: App mounts exactly one shell

**Files:**
- Modify: `drishti_web/src/App.svelte`
- Delete: `drishti_web/src/components/TabBar.svelte`
- Modify: `drishti_web/tests/shell.test.js` — remove the `describe("tab bar")` block entirely, keep `describe("offline banner")`
- Test: `drishti_web/tests/app.test.js` (append)

**Interfaces:**
- Consumes: `viewport` (Task 5), `PhoneShell` (Task 6), `DeskShell` (Task 7).
- Produces: nothing consumed later.

`TabBar.svelte` is superseded by the two shells and is deleted, not left
orphaned. Its 7 tests move to the shell tests written in Tasks 6 and 7.

- [ ] **Step 1: Write the failing test**

```js
// append to drishti_web/tests/app.test.js
import { viewport } from "../src/lib/viewport.svelte.js";

describe("shell selection", () => {
  it("mounts one shell and never both", async () => {
    // Both shells expose role="tablist". Two would mean the phone design and
    // the laptop design were on screen at once.
    session.signedIn = true;
    viewport.isDesktop = false;
    const { container } = render(App);
    expect(container.querySelectorAll('[role="tablist"]')).toHaveLength(1);
  });

  it("shows the wordmark only on the desktop shell", async () => {
    session.signedIn = true;
    viewport.isDesktop = true;
    render(App);
    expect(screen.getByText("Drishti")).toBeInTheDocument();
  });
});
```

The existing `app.test.js` already imports `App`, `session`, `render` and
`screen`; reuse those imports rather than duplicating them.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npx vitest run tests/app.test.js`
Expected: FAIL — `Drishti` wordmark not found, or two tablists

- [ ] **Step 3: Rewrite App.svelte**

```svelte
<script>
  import { session } from "./lib/session.svelte.js";
  import { house } from "./lib/app.svelte.js";
  import { viewport } from "./lib/viewport.svelte.js";
  import Login from "./routes/Login.svelte";
  import House from "./routes/House.svelte";
  import Rules from "./routes/Rules.svelte";
  import Activity from "./routes/Activity.svelte";
  import Settings from "./routes/Settings.svelte";
  import PhoneShell from "./shells/PhoneShell.svelte";
  import DeskShell from "./shells/DeskShell.svelte";
  import Composer from "./components/Composer.svelte";
  import OfflineBanner from "./components/OfflineBanner.svelte";

  let tab = $state("house");

  // One of two, never both. Each shell owns its own layout entirely, so
  // neither carries a rule written for the other.
  const Shell = $derived(viewport.isDesktop ? DeskShell : PhoneShell);

  async function handleResult(result) {
    if (result.proposal_id) {
      tab = "rules";
      await house.loadProposals();
    }
  }
</script>

{#if !session.signedIn}
  <Login />
{:else}
  <OfflineBanner offline={house.offline} />
  <Shell current={tab} onchange={(next) => (tab = next)}>
    {#if tab === "house"}<House />
    {:else if tab === "rules"}<Rules />
    {:else if tab === "activity"}<Activity />
    {:else}<Settings />{/if}
  </Shell>
  <Composer onresult={handleResult} />
{/if}
```

- [ ] **Step 4: Rename the route file**

```bash
git mv drishti_web/src/routes/Home.svelte drishti_web/src/routes/House.svelte
```

Change its heading from `<h1>Home</h1>` to `<h1>House</h1>`. Task 9 rewrites the
rest of this file; this step only keeps the app building.

- [ ] **Step 5: Delete the superseded tab bar**

```bash
git rm drishti_web/src/components/TabBar.svelte
```

Then remove the whole `describe("tab bar", ...)` block from
`drishti_web/tests/shell.test.js`, along with its now-unused `TabBar`,
`readFileSync`, `resolve`, `fireEvent` and `vi` imports. Keep
`describe("offline banner", ...)` and the imports it needs.

- [ ] **Step 6: Run the whole frontend suite**

Run: `cd drishti_web && npm test`
Expected: PASS. Count is 149 baseline − 7 removed tab-bar tests + 5 viewport + 8 phone shell + 7 desk shell + 2 app = **164 passed**, zero failures.

- [ ] **Step 7: Commit**

```bash
git add -A drishti_web
git commit -m "feat(web): mount one shell per viewport, retire the shared tab bar"
```

---

### Task 9: LiveView — a neat box, and a privacy switch

**Files:**
- Modify: `drishti_web/src/components/LiveView.svelte`
- Test: `drishti_web/tests/live-view.test.js`

**Interfaces:**
- Consumes: `POST /api/drishti/privacy` (Task 3), `api` from `src/lib/api.js`.
- Produces: `LiveView` taking `privacy` (boolean) and `onprivacy` (async function taking a boolean). Task 10 supplies both.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/live-view.test.js
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import LiveView from "../src/components/LiveView.svelte";

const props = (over = {}) => ({ privacy: false, onprivacy: () => {}, ...over });

describe("live view", () => {
  it("shows the camera when privacy is off", () => {
    render(LiveView, props());
    expect(screen.getByAltText(/live camera/i)).toBeInTheDocument();
  });

  it("shows no camera element at all when privacy is on", () => {
    // Not merely hidden: a stream element left in the DOM keeps the connection
    // open, so "off" would still be pulling frames.
    render(LiveView, props({ privacy: true }));
    expect(screen.queryByAltText(/live camera/i)).toBeNull();
  });

  it("says the camera is off rather than looking broken", () => {
    render(LiveView, props({ privacy: true }));
    expect(screen.getByText(/camera is off/i)).toBeInTheDocument();
  });

  it("offers a switch that reports the state being asked for", async () => {
    const onprivacy = vi.fn();
    render(LiveView, props({ privacy: false, onprivacy }));
    await fireEvent.click(screen.getByRole("switch", { name: /camera/i }));
    expect(onprivacy).toHaveBeenCalledWith(true);
  });

  it("reports the switch state to assistive technology", () => {
    render(LiveView, props({ privacy: true }));
    expect(screen.getByRole("switch", { name: /camera/i }))
      .toHaveAttribute("aria-checked", "true");
  });

  it("holds a fixed shape so the page does not jump when a frame arrives", () => {
    const src = readFileSync(resolve("src/components/LiveView.svelte"), "utf8");
    expect(src).toMatch(/aspect-ratio/);
    expect(src).toMatch(/object-fit:\s*cover/);
  });

  it("stacks its layers, so an overlay can be added without moving anything", () => {
    const src = readFileSync(resolve("src/components/LiveView.svelte"), "utf8");
    expect(src).toMatch(/position:\s*relative/);
  });

  it("gives the switch a 44px hit area", () => {
    const src = readFileSync(resolve("src/components/LiveView.svelte"), "utf8");
    expect(src).toMatch(/min-height:\s*44px/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npx vitest run tests/live-view.test.js`
Expected: FAIL — no `switch` role, no `camera is off` text

- [ ] **Step 3: Write minimal implementation**

```svelte
<!-- drishti_web/src/components/LiveView.svelte -->
<script>
  let { privacy, onprivacy } = $props();
  let busy = $state(false);

  async function toggle() {
    if (busy) return;
    busy = true;
    try {
      await onprivacy(!privacy);
    } finally {
      busy = false;
    }
  }
</script>

<!-- A positioned stack: the frame fills it, and anything drawn over the video
     later (detection boxes as SVG) is absolutely positioned inside without
     moving the layout. -->
<figure class="stage">
  {#if privacy}
    <div class="off">
      <p>The camera is off</p>
    </div>
  {:else}
    <img src="/api/drishti/stream" alt="Live camera view" />
  {/if}

  <button
    class="privacy"
    role="switch"
    aria-checked={privacy}
    aria-label="Camera"
    aria-busy={busy}
    onclick={toggle}
  >{privacy ? "Turn camera on" : "Turn camera off"}</button>
</figure>

<style>
  /* A fixed shape, so the layout is settled before the first frame lands and
     nothing jumps when it does. */
  .stage {
    position: relative;
    margin: 0;
    aspect-ratio: 16 / 9;
    border-radius: var(--radius-card);
    overflow: hidden;
    background: #000;
  }

  /* cover, not contain: the box is sized by the layout, and a sensor that does
     not match it should be cropped rather than letterboxed into grey bars. */
  img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .off {
    display: grid;
    place-items: center;
    height: 100%;
    color: var(--label-tertiary);
    font-size: var(--text-subhead);
  }
  .off p { margin: 0; }

  .privacy {
    position: absolute;
    right: var(--space-3);
    bottom: var(--space-3);
    min-height: 44px;
    padding: 0 var(--space-4);
    border-radius: 9999px;
    font-size: var(--text-footnote);
    font-weight: var(--weight-semibold);
    color: #fff;
    background: rgb(0 0 0 / 0.45);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }
  .privacy[aria-busy="true"] { opacity: 0.6; }

  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .privacy { background: rgb(0 0 0 / 0.9); backdrop-filter: none; -webkit-backdrop-filter: none; }
  }
</style>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npx vitest run tests/live-view.test.js`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/components/LiveView.svelte drishti_web/tests/live-view.test.js
git commit -m "feat(web): live view in a fixed box with a privacy switch"
```

---

### Task 10: House screen

**Files:**
- Modify: `drishti_web/src/routes/House.svelte`
- Modify: `drishti_web/src/components/StatusCard.svelte`
- Test: `drishti_web/tests/house.test.js`

**Interfaces:**
- Consumes: `LiveView` (Task 9), `house` store, `api`.
- Produces: nothing consumed later.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/house.test.js
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/svelte";
import StatusCard from "../src/components/StatusCard.svelte";

describe("status headline", () => {
  it("answers the question in one line when someone is home", () => {
    render(StatusCard, { state: { occupancy: "occupied", person_count: 1 } });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/someone.s home/i);
  });

  it("answers it when nobody is", () => {
    render(StatusCard, { state: { occupancy: "empty", person_count: 0 } });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/nobody.s home/i);
  });

  it("counts people when there is more than one", () => {
    render(StatusCard, { state: { occupancy: "occupied", person_count: 3 } });
    expect(screen.getByText(/3 people/i)).toBeInTheDocument();
  });

  it("says one person without a plural", () => {
    render(StatusCard, { state: { occupancy: "occupied", person_count: 1 } });
    expect(screen.getByText(/1 person/i)).toBeInTheDocument();
  });

  it("shows no uptime readout", () => {
    // How long the process has been up is a fact about the server, not about
    // the house. It went with the rest of the developer surface.
    render(StatusCard, { state: { occupancy: "empty", person_count: 0, uptime_s: 7200 } });
    expect(screen.queryByText(/running|uptime|\bh\b/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npx vitest run tests/house.test.js`
Expected: FAIL — no `heading` level 2; "Running 2 h" still present

- [ ] **Step 3: Rewrite StatusCard**

```svelte
<script>
  let { state } = $props();

  const occupied = $derived(state.occupancy === "occupied");
  const count = $derived(state.person_count ?? 0);
  const detail = $derived(
    occupied
      ? `${count} ${count === 1 ? "person" : "people"} in the room`
      : "The room is empty");
</script>

<!-- One line that answers the question, at a size you can read from across the
     room. The three stacked strings it replaced made you assemble the answer
     yourself. -->
<section>
  <h2>{occupied ? "Someone's home" : "Nobody's home"}</h2>
  <p>{detail}</p>
</section>

<style>
  section { display: grid; gap: var(--space-1); }
  h2 {
    margin: 0;
    font-size: var(--text-large-title);
    line-height: var(--lh-large-title);
    font-weight: var(--weight-bold);
    /* Negative tracking as the size grows, or the letters read too far apart. */
    letter-spacing: -0.02em;
  }
  p { margin: 0; color: var(--label-secondary); font-size: var(--text-body); }
</style>
```

- [ ] **Step 4: Update House.svelte to pass privacy through**

In the `<script>`, add:

```js
  import { api } from "../lib/api.js";

  async function setPrivacy(on) {
    await api.post("/api/drishti/privacy", { on });
    await house.loadState();
  }
```

Replace the `.top` block with:

```svelte
<h1>House</h1>

<StatusCard state={house.state} />

<LiveView privacy={!!house.state.modes?.privacy} onprivacy={setPrivacy} />

<h2 class="section-title">Devices</h2>
```

and delete the `.top` grid rule from its `<style>` — the live view is now the
hero at full column width on both shells rather than a column beside a card.

- [ ] **Step 5: Run the whole frontend suite**

Run: `cd drishti_web && npm test`
Expected: **177 passed** (164 from Task 8 + 8 live view + 5 house), zero failures.

- [ ] **Step 6: Build and deploy**

```bash
cd drishti_web && npm run build
sudo systemctl restart garuda-web.service
sleep 90
systemctl is-active garuda-web.service; cat /proc/loadavg; vcgencmd measure_temp
```

Expected: `active`, and load/temperature no worse than the Task 2 measurement.

- [ ] **Step 7: Commit**

```bash
git add -A drishti_web
git commit -m "feat(web): House screen with a glanceable headline and the camera as hero"
```

---

## Self-review

**Spec coverage.** Section 1 removals: the `Home` → `House` rename lands in
Tasks 8 and 10, and the uptime readout in Task 10. The rule-loop telemetry and
the three stub Settings sections are pass two, as sequenced — not gaps.
Section 1 addition (privacy): Tasks 3, 4, 9, 10. Section 2 (two shells): Tasks
5–8. Section 3 steps 1 and 2: Tasks 1–2; step 3 is pass two by design.
Section 4: type in Task 10, material and motion in Tasks 6–7, the three
accessibility media features asserted in Tasks 6, 7 and 9.

**Deliberately deferred, and where.** The `polish.test.js` telemetry assertions
are only touched when the telemetry is removed, which is pass two — this plan
does not modify that file, so its 5 assertions keep passing untouched.

**Type consistency.** `FramePublisher.due()`/`.encode()` as produced in Task 1
are used under those names in Task 2. `ctx.set_privacy` is declared in Task 3
and assigned in Task 4. `viewport.isDesktop` from Task 5 is read in Task 8.
Both shells take `current`, `onchange`, `children` — identical, which is what
lets Task 8's `$derived` component swap work without prop branching. `LiveView`
takes `privacy`/`onprivacy` in Task 9 and is given exactly those in Task 10.
Tab ids are `house`/`rules`/`activity`/`settings` in Tasks 6, 7 and 8 alike.

**Test counts.** 594 → 601 (Task 1) → 605 (Task 3) Python. 149 → 164 (Task 8,
net of 7 deleted tab-bar tests) → 177 (Task 10) frontend.

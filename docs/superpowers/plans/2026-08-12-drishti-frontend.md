# Drishti Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **REQUIRED BACKGROUND:** Read the `apple-hig-design` skill before any styling work. `references/web-translation.md` in that skill is the source for every token in Task 2 — do not invent values.

**Goal:** A phone-first Svelte app at `drishti.veeramanikanta.in` where the house is taught by typing a sentence, and what it knows is a shelf of cards rather than a chat transcript.

**Architecture:** Vite builds a static bundle on the Mac; the existing FastAPI app serves it, choosing between the Garuda and Drishti bundles on the `Host` header. All API calls are same-origin, because the Drishti session cookie is host-scoped and would not be sent to `api.veeramanikanta.in`. Content scrolls edge to edge beneath a glass tab bar; the composer docks above it and is reachable from every screen.

**Tech Stack:** Svelte 5 (runes), Vite 5, plain JavaScript, Vitest + @testing-library/svelte. No CSS framework — tokens come from the `apple-hig-design` skill.

**Depends on:** `docs/superpowers/plans/2026-08-12-drishti-backend.md`, Tasks 1–15. Task 1 below amends that plan.

## Global Constraints

- Same-origin API calls only. Never `https://api.veeramanikanta.in` — the `drishti_session` cookie is host-scoped and will not be sent, producing a login that appears to work and then 401s on every call.
- `credentials: "same-origin"` on every request, so the cookie is actually sent.
- No service worker in this version. The manifest gives installability; caching hashed Vite assets by hand is the classic way to ship a permanently stale app.
- Every interactive element has a hit area of at least 44×44 CSS px, even when its glyph is smaller.
- Glass material belongs to the control layer only — tab bar, composer, sheets. Never on cards or content backgrounds.
- Nested corner radii are concentric: child radius = parent radius − padding.
- No Light or Thin font weights. Regular, Medium, Semibold, Bold only.
- `prefers-reduced-motion`, `prefers-contrast` and `prefers-reduced-transparency` are all honoured.
- Colour never carries meaning alone; every coloured state also has a label or a shape.
- Run the frontend suite with `npm test` from `drishti_web/`.

---

## API Contract

Produced by backend Task 13, plus the two endpoints Task 1 below adds. Every response is JSON; every failure is `{"detail": "..."}` with a 4xx status.

| Method | Path | Response |
|---|---|---|
| POST | `/api/drishti/login` | `{ok, username, role}` |
| POST | `/api/drishti/logout` | `{ok}` |
| GET | `/api/drishti/state` | `{occupancy, person_count, mode, modes, uptime_s, pipeline, online}` |
| GET | `/api/drishti/stream` | `multipart/x-mixed-replace` MJPEG |
| GET | `/api/drishti/device-types` | `{types: {name: {actions: [], state: {}}}}` |
| GET | `/api/drishti/devices` | `{devices: [{id, name, type, room, transport, enabled, state, available}]}` |
| POST | `/api/drishti/devices` | `{ok, id}` |
| DELETE | `/api/drishti/devices/{id}` | `{ok, orphaned}` |
| POST | `/api/drishti/instruct` | one of four lane shapes, below |
| GET | `/api/drishti/proposals` | `{proposals: [{id, rule, conflict, created_at, rendered}]}` |
| POST | `/api/drishti/proposals/{id}/confirm` | `{ok}` |
| DELETE | `/api/drishti/proposals/{id}` | `{ok}` |
| GET | `/api/drishti/rules` | `{rules: [{...rule, rendered}], orphaned: []}` |
| DELETE | `/api/drishti/rules/{id}` | `{ok}` |
| POST | `/api/drishti/rules/{id}/toggle` | `{ok, enabled}` |
| GET | `/api/drishti/activity?limit=` | `{entries: [{ts, device, action, rule_id, matched, ok, reason}]}` |

The four `instruct` shapes:

```js
{ lane: "local",   ok: true,  kind: "state"|"why"|"control", text, resolved: "on-device" }
{ lane: "known",   ok: true,  resolved: "already-known", rule, rendered }
{ lane: "compile", ok: true,  resolved: "compiled", proposal_id, rule, rendered, conflict }
{ lane: "compile", ok: false, resolved: "compiled", reason, still_working, vocabulary }
```

---

## File Structure

```
drishti_web/
  package.json  vite.config.js  index.html
  public/manifest.webmanifest  public/icons/
  src/
    main.js  App.svelte
    lib/api.js              # fetch wrapper; 401 handling; same-origin
    lib/session.svelte.js   # auth state
    lib/app.svelte.js       # devices, rules, proposals, activity, state
    lib/format.js           # time, condition and device rendering
    styles/tokens.css  styles/base.css
    components/  TabBar  Composer  DeviceTile  RuleCard  ProposalCard
                 StatusCard  LiveView  EmptyState  OfflineBanner  Sheet  Confirm
    routes/      Login  Home  Rules  Activity  Settings  AddDevice
  tests/
```

Build output: `basic_pipelines/drishti_dist/`, served by FastAPI.

---

### Task 1: Backend endpoints the frontend needs

**Files:**
- Modify: `basic_pipelines/drishti_api.py`
- Modify: `tests/test_drishti_api.py`

**Interfaces:**
- Consumes: `Garuda_web` module globals for mode flags and uptime; the shared MJPEG frame buffer
- Produces: `GET /api/drishti/state`, `GET /api/drishti/stream`

This amends backend Task 13. `Garuda_web.get_state` at `/api/state` and `mjpeg_stream` at `/stream` both authenticate on the `garuda_session` cookie, so a Drishti session reaches neither. The Home screen needs both. Rather than widening Garuda's auth, Drishti gets its own two endpoints reading the same underlying data.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drishti_api.py — append

def test_state_is_served_to_a_drishti_session(client):
    test_client, ctx = client
    ctx.descriptor = {"occupancy": "occupied", "person_count": 2,
                      "temperature_c": 24.0, "humidity_pct": 50.0}
    body = test_client.get("/api/drishti/state").json()
    assert body["occupancy"] == "occupied"
    assert body["person_count"] == 2
    assert "uptime_s" in body
    assert isinstance(body["modes"], dict)


def test_state_requires_a_drishti_session(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from basic_pipelines import drishti_api
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1,),
                                    channel_to_pin={1: 17})
    app = FastAPI()
    app.include_router(drishti_api.build_router(ctx))
    assert TestClient(app).get("/api/drishti/state").status_code == 401


def test_stream_requires_a_drishti_session(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from basic_pipelines import drishti_api
    ctx = drishti_api.build_context(data_dir=str(tmp_path), relay_channels=(1,),
                                    channel_to_pin={1: 17})
    app = FastAPI()
    app.include_router(drishti_api.build_router(ctx))
    assert TestClient(app).get("/api/drishti/stream").status_code == 401


def test_a_garuda_cookie_does_not_reach_the_drishti_stream(client):
    test_client, _ = client
    test_client.cookies.clear()
    test_client.cookies.set("garuda_session", "whatever")
    assert test_client.get("/api/drishti/stream").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_drishti_api.py -k "state or stream" -v`
Expected: FAIL with 404 on `/api/drishti/state`

- [ ] **Step 3: Write minimal implementation**

Add to `build_router` in `basic_pipelines/drishti_api.py`, and add `import time` plus `from fastapi.responses import StreamingResponse` at the top:

```python
    @router.get("/state")
    async def state(session=Depends(require_drishti_session)):
        descriptor = dict(ctx.descriptor)
        modes, uptime, pipeline = {}, 0, "unknown"
        try:
            from . import Garuda_web as gw
            modes = {
                "dnd": gw.MODE_DND, "night": gw.MODE_NIGHT, "idle": gw.MODE_IDLE,
                "emergency": gw.MODE_EMERGENCY, "privacy": gw.MODE_PRIVACY,
                "email_off": gw.MODE_EMAIL_OFF,
            }
            uptime = int(time.time() - getattr(gw, "_start_time", time.time()))
            pipeline = "running" if getattr(gw, "_pipeline_alive", False) else "stopped"
        except Exception:
            # The router is built over a temp dir in tests, with no live app.
            pass
        return {
            "occupancy": descriptor.get("occupancy", "empty"),
            "person_count": descriptor.get("person_count", 0),
            "temperature_c": descriptor.get("temperature_c"),
            "humidity_pct": descriptor.get("humidity_pct"),
            "modes": modes,
            "uptime_s": uptime,
            "pipeline": pipeline,
            "online": bool(ctx.nim.api_key),
        }

    @router.get("/stream")
    async def stream(request: Request, session=Depends(require_drishti_session)):
        from .Garuda_web import mjpeg_frames
        return StreamingResponse(
            mjpeg_frames(request),
            media_type="multipart/x-mixed-replace; boundary=frame")
```

In `Garuda_web.py`, extract the existing generator so both endpoints share it. Rename the inner `generate()` of `mjpeg_stream` to a module-level `async def mjpeg_frames(request)` containing the identical body, and have `mjpeg_stream` call it after its own `garuda_session` check. Behaviour of the Garuda endpoint is unchanged; only the generator moves.

Also add `_start_time = time.time()` near the module's other globals if it does not already exist.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_drishti_api.py -v`
Expected: all pass, including the four new tests

- [ ] **Step 5: Commit**

```bash
git add basic_pipelines/drishti_api.py basic_pipelines/Garuda_web.py tests/test_drishti_api.py
git commit -m "feat(api): serve state and MJPEG to Drishti sessions

/api/state and /stream authenticate on garuda_session, which a Drishti
session does not hold. Rather than widening Garuda's auth, Drishti gets its
own two endpoints over the same frame buffer and mode flags."
```

---

### Task 2: Scaffold the app and the design tokens

**Files:**
- Create: `drishti_web/package.json`, `vite.config.js`, `index.html`, `src/main.js`, `src/App.svelte`
- Create: `drishti_web/src/styles/tokens.css`, `src/styles/base.css`
- Create: `drishti_web/public/manifest.webmanifest`
- Test: `drishti_web/tests/tokens.test.js`

**Interfaces:**
- Produces: a `npm run build` that emits to `../basic_pipelines/drishti_dist/`, and a token layer every later task consumes

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/tokens.test.js
import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";

const tokens = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");

describe("design tokens", () => {
  it("defines the iOS type scale in rem so user text size scales it", () => {
    expect(tokens).toMatch(/--text-body:\s*1\.0625rem/);
    expect(tokens).toMatch(/--text-large-title:\s*2\.125rem/);
    expect(tokens).toMatch(/--text-caption-2:\s*0\.6875rem/);
    expect(tokens).not.toMatch(/--text-[a-z0-9-]+:\s*\d+px/);
  });

  it("uses the system font stack rather than a downloaded face", () => {
    expect(tokens).toMatch(/-apple-system/);
    expect(tokens).toMatch(/system-ui/);
  });

  it("defines light and dark semantic colours", () => {
    expect(tokens).toMatch(/prefers-color-scheme:\s*dark/);
    expect(tokens).toMatch(/--label-secondary/);
    expect(tokens).toMatch(/--separator/);
  });

  it("lets an explicit theme attribute beat the OS in both directions", () => {
    expect(tokens).toMatch(/\[data-theme="dark"\]/);
    expect(tokens).toMatch(/\[data-theme="light"\]/);
  });

  it("honours reduced motion, contrast and transparency", () => {
    expect(tokens).toMatch(/prefers-reduced-motion:\s*reduce/);
    expect(tokens).toMatch(/prefers-contrast:\s*more/);
    expect(tokens).toMatch(/prefers-reduced-transparency:\s*reduce/);
  });

  it("removes backdrop-filter when transparency is reduced", () => {
    const block = tokens.slice(tokens.indexOf("prefers-reduced-transparency"));
    expect(block).toMatch(/backdrop-filter:\s*none/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test`
Expected: FAIL — no `package.json` yet

- [ ] **Step 3: Write minimal implementation**

```bash
mkdir -p drishti_web/src/{lib,styles,components,routes} drishti_web/tests drishti_web/public/icons
cd drishti_web
npm init -y
npm install -D vite @sveltejs/vite-plugin-svelte svelte vitest jsdom \
  @testing-library/svelte @testing-library/jest-dom
```

```json
// drishti_web/package.json — replace "scripts"
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "type": "module"
}
```

```js
// drishti_web/vite.config.js
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte({ hot: false })],
  base: "/drishti/",
  build: { outDir: "../basic_pipelines/drishti_dist", emptyOutDir: true },
  server: {
    proxy: { "/api/drishti": { target: "http://localhost:8080", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.js"],
  },
});
```

```js
// drishti_web/tests/setup.js
import "@testing-library/jest-dom/vitest";
```

```html
<!-- drishti_web/index.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="theme-color" content="#000000" />
    <link rel="manifest" href="/drishti/manifest.webmanifest" />
    <title>Drishti</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

```json
// drishti_web/public/manifest.webmanifest
{
  "name": "Drishti",
  "short_name": "Drishti",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "background_color": "#000000",
  "theme_color": "#000000",
  "icons": [
    { "src": "/drishti/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/drishti/icons/icon-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/drishti/icons/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```

Copy `src/styles/tokens.css` verbatim from the `apple-hig-design` skill's `references/web-translation.md` — the typeface stack, type scale, spacing and radii, safe areas, glass and standard materials with their fallbacks, colour tokens with the `data-theme` overrides, and the motion block. Do not retype the values; they are the specification.

```css
/* drishti_web/src/styles/base.css */
@import "./tokens.css";

*, *::before, *::after { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--label);
  font-size: var(--text-body);
  line-height: var(--lh-body);
  -webkit-font-smoothing: antialiased;
  /* Content extends edge to edge; the control layer floats above it. */
  overscroll-behavior-y: none;
}
button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; }
input, select { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```

```js
// drishti_web/src/main.js
import { mount } from "svelte";
import "./styles/base.css";
import App from "./App.svelte";

export default mount(App, { target: document.getElementById("app") });
```

```svelte
<!-- drishti_web/src/App.svelte -->
<script>
  let ready = $state(true);
</script>

{#if ready}
  <main>Drishti</main>
{/if}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test && npm run build`
Expected: 6 passed; build emits to `basic_pipelines/drishti_dist/`

- [ ] **Step 5: Commit**

```bash
git add drishti_web .gitignore
git commit -m "feat(web): scaffold the Drishti Svelte app with Apple HIG design tokens"
```

Add `drishti_web/node_modules/` and `basic_pipelines/drishti_dist/` to `.gitignore`.

---

### Task 3: API client

**Files:**
- Create: `drishti_web/src/lib/api.js`
- Test: `drishti_web/tests/api.test.js`

**Interfaces:**
- Produces: `api.get(path)`, `api.post(path, body)`, `api.del(path)`, `ApiError` with `.status` and `.detail`, and `onUnauthorized(handler)`

Every call is same-origin and sends the cookie. A 401 fires the unauthorized handler once so the app returns to the login screen instead of surfacing an error on every card.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/api.test.js
import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, ApiError, onUnauthorized } from "../src/lib/api.js";

function respond(status, body) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => { onUnauthorized(null); });

describe("api client", () => {
  it("calls a same-origin path and sends the cookie", async () => {
    const fetchMock = vi.fn(() => respond(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/api/drishti/devices");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/drishti/devices");
    expect(url.startsWith("http")).toBe(false);
    expect(options.credentials).toBe("same-origin");
  });

  it("posts JSON with the right content type", async () => {
    const fetchMock = vi.fn(() => respond(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.post("/api/drishti/instruct", { text: "hello" });

    const [, options] = fetchMock.mock.calls[0];
    expect(options.method).toBe("POST");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(options.body)).toEqual({ text: "hello" });
  });

  it("throws ApiError carrying the server's detail", async () => {
    vi.stubGlobal("fetch", () => respond(400, { detail: "channel 99 is not one of [1, 2, 3]" }));
    await expect(api.post("/api/drishti/devices", {})).rejects.toMatchObject({
      status: 400,
      detail: "channel 99 is not one of [1, 2, 3]",
    });
  });

  it("fires the unauthorized handler on 401", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", () => respond(401, { detail: "not signed in" }));

    await expect(api.get("/api/drishti/rules")).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("reports a network failure as an offline ApiError", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.get("/api/drishti/state")).rejects.toMatchObject({
      status: 0,
      offline: true,
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/api.test.js`
Expected: FAIL — cannot resolve `../src/lib/api.js`

- [ ] **Step 3: Write minimal implementation**

```js
// drishti_web/src/lib/api.js
// Same-origin only. The drishti_session cookie is host-scoped, so a call to
// api.veeramanikanta.in would not carry it -- login would appear to succeed
// and every subsequent request would 401.

export class ApiError extends Error {
  constructor(status, detail, offline = false) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.offline = offline;
  }
}

let unauthorizedHandler = null;
export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...options,
    });
  } catch {
    throw new ApiError(0, "Can't reach the house right now.", true);
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    if (response.status === 401 && unauthorizedHandler) unauthorizedHandler();
    throw new ApiError(response.status, body?.detail ?? "Something went wrong.");
  }
  return body;
}

export const api = {
  get: (path) => request(path),
  post: (path, body) =>
    request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }),
  del: (path) => request(path, { method: "DELETE" }),
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test tests/api.test.js`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/lib/api.js drishti_web/tests/api.test.js
git commit -m "feat(web): same-origin API client with 401 and offline handling"
```

---

### Task 4: Session state and login

**Files:**
- Create: `drishti_web/src/lib/session.svelte.js`, `src/routes/Login.svelte`
- Test: `drishti_web/tests/login.test.js`

**Interfaces:**
- Consumes: `api.post`, `onUnauthorized`
- Produces: `session` object with reactive `username`, `role`, `signedIn`; `signIn(username, password)`, `signOut()`, `clear()`

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/login.test.js
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import Login from "../src/routes/Login.svelte";
import { session } from "../src/lib/session.svelte.js";

beforeEach(() => session.clear());

describe("login", () => {
  it("signs in and records the role", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({ ok: true, status: 200,
        json: () => Promise.resolve({ ok: true, username: "mani", role: "admin" }) }));

    render(Login);
    await fireEvent.input(screen.getByLabelText(/username/i), { target: { value: "mani" } });
    await fireEvent.input(screen.getByLabelText(/password/i), { target: { value: "pw" } });
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(session.signedIn).toBe(true));
    expect(session.role).toBe("admin");
  });

  it("shows the server's message on a bad password", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({ ok: false, status: 401,
        json: () => Promise.resolve({ detail: "invalid credentials" }) }));

    render(Login);
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid credentials/i);
    expect(session.signedIn).toBe(false);
  });

  it("says the house is unreachable when the network is down", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));

    render(Login);
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/can't reach the house/i);
  });

  it("password field is a password input", () => {
    render(Login);
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("type", "password");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/login.test.js`
Expected: FAIL — cannot resolve `../src/routes/Login.svelte`

- [ ] **Step 3: Write minimal implementation**

```js
// drishti_web/src/lib/session.svelte.js
import { api, onUnauthorized } from "./api.js";

class Session {
  username = $state("");
  role = $state("");
  signedIn = $state(false);

  clear() {
    this.username = "";
    this.role = "";
    this.signedIn = false;
  }

  async signIn(username, password) {
    const body = await api.post("/api/drishti/login", { username, password });
    this.username = body.username;
    this.role = body.role;
    this.signedIn = true;
  }

  async signOut() {
    try {
      await api.post("/api/drishti/logout");
    } finally {
      this.clear();
    }
  }
}

export const session = new Session();

onUnauthorized(() => session.clear());
```

```svelte
<!-- drishti_web/src/routes/Login.svelte -->
<script>
  import { session } from "../lib/session.svelte.js";

  let username = $state("");
  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  async function submit(event) {
    event.preventDefault();
    error = "";
    busy = true;
    try {
      await session.signIn(username, password);
    } catch (err) {
      error = err.detail ?? "Something went wrong.";
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <form onsubmit={submit}>
    <h1>Drishti</h1>
    <p class="sub">Sign in to your house.</p>

    <label for="u">Username</label>
    <input id="u" bind:value={username} autocomplete="username" autocapitalize="none" />

    <label for="p">Password</label>
    <input id="p" type="password" bind:value={password} autocomplete="current-password" />

    {#if error}<p class="err" role="alert">{error}</p>{/if}

    <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
  </form>
</div>

<style>
  .wrap {
    min-height: 100dvh;
    display: grid;
    place-items: center;
    padding: max(var(--space-6), env(safe-area-inset-top)) var(--space-4);
  }
  form { width: 100%; max-width: 22rem; display: grid; gap: var(--space-2); }
  h1 { font-size: var(--text-large-title); line-height: var(--lh-large-title); margin: 0; font-weight: 600; }
  .sub { color: var(--label-secondary); margin: 0 0 var(--space-4); }
  label { font-size: var(--text-subhead); color: var(--label-secondary); margin-top: var(--space-2); }
  input {
    min-height: 44px;
    padding: 0 var(--space-3);
    border-radius: var(--radius-control);
    border: 1px solid var(--separator);
    background: var(--bg-secondary);
  }
  .err { color: var(--danger); font-size: var(--text-footnote); margin: var(--space-2) 0 0; }
  button {
    margin-top: var(--space-5);
    min-height: 44px;
    border-radius: var(--radius-control);
    background: var(--accent);
    color: #fff;
    font-weight: 600;
  }
  button:disabled { opacity: 0.5; }
</style>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test tests/login.test.js`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/lib/session.svelte.js drishti_web/src/routes/Login.svelte drishti_web/tests/login.test.js
git commit -m "feat(web): Drishti sign-in and session state"
```

---

### Task 5: App shell — tab bar and content layer

**Files:**
- Create: `drishti_web/src/components/TabBar.svelte`, `src/components/OfflineBanner.svelte`
- Modify: `drishti_web/src/App.svelte`
- Test: `drishti_web/tests/shell.test.js`

**Interfaces:**
- Consumes: `session`
- Produces: `TabBar` with props `{ current, onchange }`; `App` routing between `home | rules | activity | settings`

Four tabs for every role. Settings gates its contents by role rather than the tab disappearing, so the app does not change shape depending on who is signed in.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/shell.test.js
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import TabBar from "../src/components/TabBar.svelte";
import OfflineBanner from "../src/components/OfflineBanner.svelte";

describe("tab bar", () => {
  it("shows exactly the four tabs", () => {
    render(TabBar, { current: "home", onchange: () => {} });
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent.trim())).toEqual(
      ["Home", "Rules", "Activity", "Settings"]);
  });

  it("marks the current tab as selected", () => {
    render(TabBar, { current: "rules", onchange: () => {} });
    expect(screen.getByRole("tab", { name: "Rules" }))
      .toHaveAttribute("aria-selected", "true");
  });

  it("reports a tab change", async () => {
    const onchange = vi.fn();
    render(TabBar, { current: "home", onchange });
    await fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(onchange).toHaveBeenCalledWith("activity");
  });

  it("has no emergency stop among the navigation targets", () => {
    render(TabBar, { current: "home", onchange: () => {} });
    expect(screen.queryByRole("tab", { name: /stop|emergency/i })).toBeNull();
  });

  it("gives every tab a 44px hit area", () => {
    render(TabBar, { current: "home", onchange: () => {} });
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab).toHaveStyle({ minHeight: "44px" });
    }
  });
});

describe("offline banner", () => {
  it("says what still works", () => {
    render(OfflineBanner, { offline: true });
    const alert = screen.getByRole("status");
    expect(alert).toHaveTextContent(/rules are still running/i);
  });

  it("shows nothing when online", () => {
    render(OfflineBanner, { offline: false });
    expect(screen.queryByRole("status")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/shell.test.js`
Expected: FAIL — cannot resolve `../src/components/TabBar.svelte`

- [ ] **Step 3: Write minimal implementation**

```svelte
<!-- drishti_web/src/components/TabBar.svelte -->
<script>
  let { current, onchange } = $props();

  const TABS = [
    { id: "home",     label: "Home",     d: "M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z" },
    { id: "rules",    label: "Rules",    d: "M4 6h16M4 12h16M4 18h10" },
    { id: "activity", label: "Activity", d: "M3 12h4l3 8 4-16 3 8h4" },
    { id: "settings", label: "Settings", d: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 2.6 7" },
  ];
</script>

<nav role="tablist" aria-label="Sections">
  {#each TABS as tab}
    <button
      role="tab"
      aria-selected={current === tab.id}
      onclick={() => onchange(tab.id)}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d={tab.d} />
      </svg>
      <span>{tab.label}</span>
    </button>
  {/each}
</nav>

<style>
  /* Control layer: floats above content, which scrolls beneath it. */
  nav {
    position: fixed;
    inset: auto 0 0 0;
    z-index: 20;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    padding: var(--space-1) var(--space-2);
    padding-bottom: max(var(--space-1), env(safe-area-inset-bottom));
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border-top: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
  }
  button {
    min-height: 44px;
    display: grid;
    justify-items: center;
    gap: 2px;
    padding: var(--space-1) 0;
    color: var(--label-secondary);
    transition: color var(--dur-fast) var(--ease-standard);
  }
  button[aria-selected="true"] { color: var(--accent); }
  svg { width: 24px; height: 24px; }
  span { font-size: var(--text-caption-2); line-height: var(--lh-caption-2); font-weight: 500; }

  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    nav { backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--surface); }
  }
</style>
```

```svelte
<!-- drishti_web/src/components/OfflineBanner.svelte -->
<script>
  let { offline } = $props();
</script>

{#if offline}
  <p role="status">
    Can't reach the network. Your rules are still running, and you can still
    control devices and ask about the house — new rules need a connection.
  </p>
{/if}

<style>
  /* Offline is a state, not an error: it says what still works. */
  p {
    margin: 0;
    padding: var(--space-2) var(--margin-content);
    background: color-mix(in srgb, var(--warning) 18%, var(--bg));
    color: var(--label);
    font-size: var(--text-footnote);
    line-height: var(--lh-footnote);
  }
</style>
```

```svelte
<!-- drishti_web/src/App.svelte -->
<script>
  import { session } from "./lib/session.svelte.js";
  import Login from "./routes/Login.svelte";
  import TabBar from "./components/TabBar.svelte";
  import OfflineBanner from "./components/OfflineBanner.svelte";

  let tab = $state("home");
  let offline = $state(false);
</script>

{#if !session.signedIn}
  <Login />
{:else}
  <OfflineBanner {offline} />
  <main>
    {#if tab === "home"}<h1>Home</h1>
    {:else if tab === "rules"}<h1>Rules</h1>
    {:else if tab === "activity"}<h1>Activity</h1>
    {:else}<h1>Settings</h1>{/if}
  </main>
  <TabBar current={tab} onchange={(next) => (tab = next)} />
{/if}

<style>
  /* Content runs to every edge and scrolls under the control layer. */
  main {
    min-height: 100dvh;
    padding: var(--space-4) var(--margin-content);
    padding-top: max(var(--space-4), env(safe-area-inset-top));
    padding-bottom: calc(140px + env(safe-area-inset-bottom));
  }
  h1 { font-size: var(--text-large-title); line-height: var(--lh-large-title); font-weight: 700; margin: 0 0 var(--space-4); }
</style>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test tests/shell.test.js`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/components drishti_web/src/App.svelte drishti_web/tests/shell.test.js
git commit -m "feat(web): app shell with glass tab bar and offline banner

Emergency Stop is deliberately absent from the tab bar. In Garuda it sits in
the same scrolling row used to switch pages, where a mis-tap triggers an
irreversible action; it moves to Home with a confirmation step."
```

---

### Task 6: Composer

**Files:**
- Create: `drishti_web/src/components/Composer.svelte`, `src/lib/app.svelte.js`
- Test: `drishti_web/tests/composer.test.js`

**Interfaces:**
- Consumes: `api.post`
- Produces: `Composer` with prop `{ onresult }`; `house` store with `proposals`, `rules`, `devices`, `activity`, `state`, and loaders for each

The composer is the whole interaction model. It never renders a transcript: a typed sentence either produces an immediate answer, or becomes a card that lands in Rules.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/composer.test.js
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import Composer from "../src/components/Composer.svelte";

function stub(body, status = 200) {
  vi.stubGlobal("fetch", () =>
    Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) }));
}

async function type(text) {
  await fireEvent.input(screen.getByRole("textbox"), { target: { value: text } });
  await fireEvent.click(screen.getByRole("button", { name: /send/i }));
}

describe("composer", () => {
  it("shows a local answer inline and clears the field", async () => {
    stub({ lane: "local", ok: true, kind: "state",
           text: "Yes — 1 person in the room right now.", resolved: "on-device" });
    render(Composer, { onresult: () => {} });

    await type("is anyone home?");

    expect(await screen.findByText(/1 person in the room/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("marks a local answer as resolved on device", async () => {
    stub({ lane: "local", ok: true, kind: "state", text: "It is 24°C.", resolved: "on-device" });
    render(Composer, { onresult: () => {} });
    await type("what's the temperature?");
    expect(await screen.findByText(/on device/i)).toBeInTheDocument();
  });

  it("hands a compiled proposal to the parent instead of rendering a bubble", async () => {
    const onresult = vi.fn();
    stub({ lane: "compile", ok: true, resolved: "compiled", proposal_id: "abc",
           rule: { source_utterance: "turn the fan off when the room is empty" },
           rendered: { when: "occupancy == empty", then: "fan → off" }, conflict: null });
    render(Composer, { onresult });

    await type("turn the fan off when the room is empty");

    await waitFor(() => expect(onresult).toHaveBeenCalled());
    expect(onresult.mock.calls[0][0].proposal_id).toBe("abc");
    expect(screen.queryByText(/turn the fan off when/)).toBeNull();
  });

  it("shows the refusal reason and says rules still fire", async () => {
    stub({ lane: "compile", ok: false, resolved: "compiled",
           reason: "could not reach the rule service: ConnectionError",
           still_working: true, vocabulary: ["occupancy", "hour"] });
    render(Composer, { onresult: () => {} });

    await type("dim the hallway when it rains");

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the rule service/);
    expect(screen.getByRole("alert")).toHaveTextContent(/still running/i);
  });

  it("reports an already-known rule rather than compiling again", async () => {
    stub({ lane: "known", ok: true, resolved: "already-known",
           rule: { id: "r_1", source_utterance: "turn the fan off when the room is empty" },
           rendered: { when: "occupancy == empty", then: "fan → off" } });
    render(Composer, { onresult: () => {} });

    await type("when the room's empty switch the fan off");

    expect(await screen.findByText(/already knows/i)).toBeInTheDocument();
  });

  it("refuses to send an empty instruction", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(Composer, { onresult: () => {} });
    await fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/composer.test.js`
Expected: FAIL — cannot resolve `../src/components/Composer.svelte`

- [ ] **Step 3: Write minimal implementation**

```js
// drishti_web/src/lib/app.svelte.js
import { api } from "./api.js";

class House {
  devices = $state([]);
  rules = $state([]);
  orphaned = $state([]);
  proposals = $state([]);
  activity = $state([]);
  state = $state({});
  offline = $state(false);

  async #load(path, apply) {
    try {
      apply(await api.get(path));
      this.offline = false;
    } catch (err) {
      if (err.offline) this.offline = true;
      else throw err;
    }
  }

  loadDevices()   { return this.#load("/api/drishti/devices",   (b) => (this.devices = b.devices)); }
  loadProposals() { return this.#load("/api/drishti/proposals", (b) => (this.proposals = b.proposals)); }
  loadActivity()  { return this.#load("/api/drishti/activity",  (b) => (this.activity = b.entries)); }
  loadState()     { return this.#load("/api/drishti/state",     (b) => (this.state = b)); }
  loadRules() {
    return this.#load("/api/drishti/rules", (b) => {
      this.rules = b.rules;
      this.orphaned = b.orphaned;
    });
  }
}

export const house = new House();
```

```svelte
<!-- drishti_web/src/components/Composer.svelte -->
<script>
  import { api } from "../lib/api.js";

  let { onresult } = $props();

  let text = $state("");
  let busy = $state(false);
  let answer = $state(null);   // a local or already-known result
  let failure = $state(null);  // a compile refusal

  async function send(event) {
    event?.preventDefault();
    const instruction = text.trim();
    if (!instruction || busy) return;

    busy = true;
    answer = null;
    failure = null;
    try {
      const result = await api.post("/api/drishti/instruct", { text: instruction });
      text = "";
      if (result.lane === "compile" && result.ok) {
        // A proposal is a card, not a message. The parent shelves it.
        onresult(result);
      } else if (result.lane === "compile") {
        failure = result;
      } else {
        answer = result;
      }
    } catch (err) {
      failure = { reason: err.detail, still_working: err.offline };
    } finally {
      busy = false;
    }
  }
</script>

<div class="dock">
  {#if answer}
    <div class="answer">
      <p>{answer.lane === "known"
        ? `The house already knows this — “${answer.rule.source_utterance}”.`
        : answer.text}</p>
      <span class="mark">{answer.lane === "known" ? "Already known" : "Answered on device"}</span>
    </div>
  {/if}

  {#if failure}
    <div class="answer failure" role="alert">
      <p>{failure.reason}</p>
      {#if failure.still_working}
        <span class="mark">Your rules are still running.</span>
      {/if}
    </div>
  {/if}

  <form onsubmit={send}>
    <input
      type="text"
      bind:value={text}
      placeholder="Tell the house what to do"
      aria-label="Tell the house what to do"
      enterkeyhint="send"
      autocapitalize="sentences"
    />
    <button type="submit" aria-label="Send" disabled={busy}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 19V5M5 12l7-7 7 7" />
      </svg>
    </button>
  </form>
</div>

<style>
  /* Docks above the tab bar and is reachable from every screen. The composer
     is an action, not a place -- it is never a tab, and never a transcript. */
  .dock {
    position: fixed;
    inset: auto 0 calc(60px + env(safe-area-inset-bottom)) 0;
    z-index: 21;
    padding: 0 var(--margin-content) var(--space-2);
    display: grid;
    gap: var(--space-2);
  }
  form {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: var(--space-2);
    align-items: center;
    padding: var(--space-1);
    border-radius: 9999px;
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
  }
  input {
    min-height: 44px;
    padding: 0 var(--space-3);
    border: 0;
    background: none;
  }
  input:focus { outline: none; }
  button {
    min-width: 44px;
    min-height: 44px;
    display: grid;
    place-items: center;
    border-radius: 9999px;
    background: var(--accent);
    color: #fff;
  }
  button:disabled { opacity: 0.5; }
  svg { width: 20px; height: 20px; }

  .answer {
    padding: var(--space-3);
    border-radius: var(--radius-card);
    background: var(--bg-secondary);
    border: 0.5px solid var(--separator);
  }
  .answer p { margin: 0; }
  .failure { border-color: color-mix(in srgb, var(--danger) 50%, var(--separator)); }
  .mark {
    display: block;
    margin-top: var(--space-1);
    font-size: var(--text-caption-1);
    color: var(--label-secondary);
  }

  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    form { backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--surface); }
  }
</style>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test tests/composer.test.js`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/components/Composer.svelte drishti_web/src/lib/app.svelte.js drishti_web/tests/composer.test.js
git commit -m "feat(web): composer that answers inline or produces a rule card"
```

---

### Task 7: Rule and proposal cards

**Files:**
- Create: `drishti_web/src/components/RuleCard.svelte`, `src/components/ProposalCard.svelte`, `src/lib/format.js`
- Test: `drishti_web/tests/cards.test.js`

**Interfaces:**
- Consumes: `rendered` from the API, `format.relativeTime`, `format.deviceName`
- Produces: `RuleCard` props `{ rule, devices, ontoggle, ondelete }`; `ProposalCard` props `{ proposal, onconfirm, ondiscard }`

A proposal never saves itself. The model can return a rule that passes every check and still means the opposite of what was asked, and the confirm step is the only thing that catches it.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/cards.test.js
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import RuleCard from "../src/components/RuleCard.svelte";
import ProposalCard from "../src/components/ProposalCard.svelte";

const DEVICES = [{ id: "fan", name: "Fan", room: "study" }];

const RULE = {
  id: "r_1",
  source_utterance: "turn the fan off when the room is empty for five minutes",
  rendered: { when: "occupancy == empty and occupancy_duration_s >= 300", then: "fan → off" },
  enabled: true,
  fired_count: 12,
  last_fired: 1_754_000_000,
};

describe("rule card", () => {
  it("uses the spoken sentence as the title", () => {
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle: () => {}, ondelete: () => {} });
    expect(screen.getByRole("heading"))
      .toHaveTextContent("turn the fan off when the room is empty for five minutes");
  });

  it("shows the conditions and actions as chips", () => {
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle: () => {}, ondelete: () => {} });
    expect(screen.getByText(/occupancy == empty/)).toBeInTheDocument();
    expect(screen.getByText(/fan → off/)).toBeInTheDocument();
  });

  it("reports how often it has fired", () => {
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle: () => {}, ondelete: () => {} });
    expect(screen.getByText(/12 times/i)).toBeInTheDocument();
  });

  it("toggles", async () => {
    const ontoggle = vi.fn();
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle, ondelete: () => {} });
    await fireEvent.click(screen.getByRole("switch"));
    expect(ontoggle).toHaveBeenCalledWith("r_1");
  });

  it("marks an orphaned rule and does not offer to enable it", () => {
    render(RuleCard, {
      rule: { ...RULE, orphaned: true, enabled: false },
      devices: [], ontoggle: () => {}, ondelete: () => {},
    });
    expect(screen.getByText(/needs repair/i)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("states enabled in words, not only colour", () => {
    render(RuleCard, {
      rule: { ...RULE, enabled: false }, devices: DEVICES,
      ontoggle: () => {}, ondelete: () => {},
    });
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/paused/i)).toBeInTheDocument();
  });
});

describe("proposal card", () => {
  const PROPOSAL = {
    id: "abc",
    rule: { source_utterance: "turn the fan off when the room is empty" },
    rendered: { when: "occupancy == empty", then: "fan → off" },
    conflict: null,
  };

  it("asks before saving", () => {
    render(ProposalCard, { proposal: PROPOSAL, onconfirm: () => {}, ondiscard: () => {} });
    expect(screen.getByText(/here's what i understood/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /discard/i })).toBeInTheDocument();
  });

  it("confirms and discards through callbacks", async () => {
    const onconfirm = vi.fn();
    const ondiscard = vi.fn();
    render(ProposalCard, { proposal: PROPOSAL, onconfirm, ondiscard });
    await fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onconfirm).toHaveBeenCalledWith("abc");
    await fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    expect(ondiscard).toHaveBeenCalledWith("abc");
  });

  it("shows both rules when there is a conflict", () => {
    render(ProposalCard, {
      proposal: { ...PROPOSAL, conflict: { id: "r_9", source_utterance: "keep the fan on while I'm at the desk" } },
      onconfirm: () => {}, ondiscard: () => {},
    });
    expect(screen.getByText(/conflicts with/i)).toBeInTheDocument();
    expect(screen.getByText(/keep the fan on while I'm at the desk/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/cards.test.js`
Expected: FAIL — cannot resolve `../src/components/RuleCard.svelte`

- [ ] **Step 3: Write minimal implementation**

```js
// drishti_web/src/lib/format.js
export function relativeTime(seconds) {
  if (!seconds) return "never";
  const delta = Math.max(0, Date.now() / 1000 - seconds);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`;
  if (delta < 86_400) return `${Math.floor(delta / 3600)} h ago`;
  return `${Math.floor(delta / 86_400)} d ago`;
}

export function deviceName(devices, id) {
  return devices.find((d) => d.id === id)?.name ?? id;
}

export function firedCount(count) {
  if (!count) return "never fired";
  return count === 1 ? "fired once" : `fired ${count} times`;
}
```

```svelte
<!-- drishti_web/src/components/RuleCard.svelte -->
<script>
  import { relativeTime, firedCount } from "../lib/format.js";
  let { rule, devices, ontoggle, ondelete } = $props();
</script>

<article class:orphaned={rule.orphaned}>
  <h3>{rule.source_utterance}</h3>

  <div class="chips">
    <span class="chip when">When {rule.rendered?.when}</span>
    <span class="chip then">Then {rule.rendered?.then}</span>
  </div>

  <footer>
    {#if rule.orphaned}
      <span class="repair">Needs repair — a device it uses was removed</span>
    {:else}
      <span class="meta">{firedCount(rule.fired_count)} · {relativeTime(rule.last_fired)}</span>
      <button
        role="switch"
        aria-checked={rule.enabled}
        aria-label="Enabled"
        onclick={() => ontoggle(rule.id)}
      >
        <span class="track" aria-hidden="true"></span>
        <span class="word">{rule.enabled ? "On" : "Paused"}</span>
      </button>
    {/if}
    <button class="del" onclick={() => ondelete(rule.id)} aria-label="Delete rule">Delete</button>
  </footer>
</article>

<style>
  /* Content layer -- a solid surface, never glass. */
  article {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
    padding: var(--space-3);
    display: grid;
    gap: var(--space-2);
  }
  article.orphaned { border-color: color-mix(in srgb, var(--warning) 60%, var(--separator)); }
  h3 {
    margin: 0;
    font-size: var(--text-headline);
    line-height: var(--lh-headline);
    font-weight: 600;
  }
  .chips { display: flex; flex-wrap: wrap; gap: var(--space-2); }
  .chip {
    /* Concentric: parent radius 20 minus 12 padding. */
    border-radius: calc(var(--radius-card) - var(--space-3));
    padding: var(--space-1) var(--space-2);
    font-size: var(--text-footnote);
    background: var(--bg-secondary);
    color: var(--label-secondary);
  }
  footer { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
  .meta, .repair { font-size: var(--text-caption-1); color: var(--label-secondary); }
  [role="switch"] { min-height: 44px; display: flex; align-items: center; gap: var(--space-2); }
  .track {
    width: 42px; height: 26px; border-radius: 9999px;
    background: var(--separator);
    position: relative;
    transition: background var(--dur-fast) var(--ease-standard);
  }
  .track::after {
    content: ""; position: absolute; top: 2px; left: 2px;
    width: 22px; height: 22px; border-radius: 9999px; background: #fff;
    transition: transform var(--dur-fast) var(--ease-standard);
  }
  [aria-checked="true"] .track { background: var(--success); }
  [aria-checked="true"] .track::after { transform: translateX(16px); }
  /* The word carries the state too -- colour alone never does. */
  .word { font-size: var(--text-caption-1); color: var(--label-secondary); }
  .del { min-height: 44px; margin-left: auto; color: var(--danger); font-size: var(--text-footnote); }
</style>
```

```svelte
<!-- drishti_web/src/components/ProposalCard.svelte -->
<script>
  let { proposal, onconfirm, ondiscard } = $props();
</script>

<article>
  <p class="lede">Here's what I understood.</p>
  <h3>{proposal.rule.source_utterance}</h3>

  <div class="chips">
    <span class="chip">When {proposal.rendered?.when}</span>
    <span class="chip">Then {proposal.rendered?.then}</span>
  </div>

  {#if proposal.conflict}
    <div class="conflict" role="alert">
      <strong>Conflicts with a rule you already have.</strong>
      <p>{proposal.conflict.source_utterance}</p>
      <p class="hint">Both drive the same device the opposite way. Saving keeps both — the earlier rule wins when they overlap.</p>
    </div>
  {/if}

  <div class="actions">
    <button class="save" onclick={() => onconfirm(proposal.id)}>Save</button>
    <button class="discard" onclick={() => ondiscard(proposal.id)}>Discard</button>
  </div>
</article>

<style>
  article {
    background: var(--surface);
    border: 1px solid var(--accent);
    border-radius: var(--radius-card);
    padding: var(--space-3);
    display: grid;
    gap: var(--space-2);
  }
  .lede { margin: 0; font-size: var(--text-footnote); color: var(--label-secondary); }
  h3 { margin: 0; font-size: var(--text-headline); line-height: var(--lh-headline); font-weight: 600; }
  .chips { display: flex; flex-wrap: wrap; gap: var(--space-2); }
  .chip {
    border-radius: calc(var(--radius-card) - var(--space-3));
    padding: var(--space-1) var(--space-2);
    font-size: var(--text-footnote);
    background: var(--bg-secondary);
    color: var(--label-secondary);
  }
  .conflict {
    border-radius: calc(var(--radius-card) - var(--space-3));
    padding: var(--space-2);
    background: color-mix(in srgb, var(--warning) 16%, var(--bg));
  }
  .conflict p { margin: var(--space-1) 0 0; font-size: var(--text-footnote); }
  .hint { color: var(--label-secondary); }
  .actions { display: flex; gap: var(--space-2); }
  .actions button { min-height: 44px; flex: 1; border-radius: var(--radius-control); font-weight: 600; }
  .save { background: var(--accent); color: #fff; }
  .discard { background: var(--bg-secondary); color: var(--label); }
</style>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test tests/cards.test.js`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/components/RuleCard.svelte drishti_web/src/components/ProposalCard.svelte drishti_web/src/lib/format.js drishti_web/tests/cards.test.js
git commit -m "feat(web): rule and proposal cards with confirm-before-save"
```

---

### Task 8: Home screen

**Files:**
- Create: `drishti_web/src/routes/Home.svelte`, `src/components/StatusCard.svelte`, `src/components/DeviceTile.svelte`, `src/components/LiveView.svelte`, `src/components/Confirm.svelte`
- Test: `drishti_web/tests/home.test.js`

**Interfaces:**
- Consumes: `house`, `api`
- Produces: `Home` with no props

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/home.test.js
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import DeviceTile from "../src/components/DeviceTile.svelte";
import StatusCard from "../src/components/StatusCard.svelte";
import Confirm from "../src/components/Confirm.svelte";

describe("device tile", () => {
  const LAMP = { id: "lamp_desk", name: "Desk lamp", room: "study",
                 type: "light", state: "off", available: true };

  it("names the device and its room", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {} });
    expect(screen.getByText("Desk lamp")).toBeInTheDocument();
    expect(screen.getByText(/study/i)).toBeInTheDocument();
  });

  it("states its state in words", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {} });
    expect(screen.getByText(/^off$/i)).toBeInTheDocument();
  });

  it("toggles to the opposite state", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, { device: LAMP, ontoggle });
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).toHaveBeenCalledWith("lamp_desk", "on");
  });

  it("marks an unreachable device and refuses to toggle it", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, { device: { ...LAMP, available: false }, ontoggle });
    expect(screen.getByText(/unreachable/i)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).not.toHaveBeenCalled();
  });

  it("has a 44px hit area", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {} });
    expect(screen.getByRole("button")).toHaveStyle({ minHeight: "44px" });
  });
});

describe("status card", () => {
  it("says the room is occupied and by how many", () => {
    render(StatusCard, { state: { occupancy: "occupied", person_count: 2, uptime_s: 7200 } });
    expect(screen.getByText(/occupied/i)).toBeInTheDocument();
    expect(screen.getByText(/2/)).toBeInTheDocument();
  });

  it("renders uptime in hours", () => {
    render(StatusCard, { state: { occupancy: "empty", person_count: 0, uptime_s: 7200 } });
    expect(screen.getByText(/2 h/)).toBeInTheDocument();
  });
});

describe("confirm", () => {
  it("requires an explicit confirmation before the destructive action", async () => {
    const onconfirm = vi.fn();
    render(Confirm, {
      open: true, title: "Stop everything?",
      body: "This cuts power to every device and halts detection.",
      confirmLabel: "Stop", onconfirm, oncancel: () => {},
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onconfirm).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onconfirm).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/home.test.js`
Expected: FAIL — cannot resolve `../src/components/DeviceTile.svelte`

- [ ] **Step 3: Write minimal implementation**

```svelte
<!-- drishti_web/src/components/DeviceTile.svelte -->
<script>
  let { device, ontoggle } = $props();
  const next = $derived(device.state === "on" ? "off" : "on");
  const actuator = $derived(!device.type.startsWith("sensor."));
</script>

<button
  onclick={() => device.available && actuator && ontoggle(device.id, next)}
  aria-disabled={!device.available}
  class:on={device.state === "on"}
>
  <span class="name">{device.name}</span>
  <span class="room">{device.room}</span>
  {#if device.available}
    <span class="state">{device.state}</span>
  {:else}
    <span class="state warn">Unreachable</span>
  {/if}
</button>

<style>
  button {
    min-height: 44px;
    display: grid;
    gap: 2px;
    padding: var(--space-3);
    text-align: left;
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
  }
  button.on { background: color-mix(in srgb, var(--accent) 14%, var(--surface)); }
  button[aria-disabled="true"] { opacity: 0.55; }
  .name { font-size: var(--text-callout); font-weight: 600; }
  .room, .state { font-size: var(--text-caption-1); color: var(--label-secondary); text-transform: capitalize; }
  .warn { color: var(--warning); }
</style>
```

```svelte
<!-- drishti_web/src/components/StatusCard.svelte -->
<script>
  let { state } = $props();
  const hours = $derived(Math.floor((state.uptime_s ?? 0) / 3600));
</script>

<section>
  <p class="headline">
    {state.occupancy === "occupied" ? "Someone's home" : "Nobody's home"}
  </p>
  <p class="detail">
    {state.occupancy === "occupied"
      ? `${state.person_count} in the room`
      : "The room is empty"}
  </p>
  <p class="meta">Running {hours} h</p>
</section>

<style>
  section {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
    padding: var(--space-4);
    display: grid;
    gap: var(--space-1);
  }
  .headline { margin: 0; font-size: var(--text-title-2); line-height: var(--lh-title-2); font-weight: 600; }
  .detail { margin: 0; color: var(--label-secondary); }
  .meta { margin: 0; font-size: var(--text-caption-1); color: var(--label-tertiary); }
</style>
```

```svelte
<!-- drishti_web/src/components/Confirm.svelte -->
<script>
  let { open, title, body, confirmLabel, onconfirm, oncancel } = $props();
</script>

{#if open}
  <div class="scrim" role="presentation" onclick={oncancel}>
    <div class="sheet" role="dialog" aria-modal="true" aria-label={title}
         onclick={(event) => event.stopPropagation()}>
      <h2>{title}</h2>
      <p>{body}</p>
      <div class="actions">
        <button class="cancel" onclick={oncancel}>Cancel</button>
        <button class="go" onclick={onconfirm}>{confirmLabel}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed; inset: 0; z-index: 40;
    display: grid; align-items: end;
    background: rgb(0 0 0 / 0.4);
  }
  .sheet {
    background: var(--surface);
    border-radius: var(--radius-sheet) var(--radius-sheet) 0 0;
    padding: var(--space-5) var(--margin-content);
    padding-bottom: max(var(--space-5), env(safe-area-inset-bottom));
    display: grid; gap: var(--space-2);
  }
  h2 { margin: 0; font-size: var(--text-title-3); font-weight: 600; }
  p { margin: 0; color: var(--label-secondary); }
  .actions { display: flex; gap: var(--space-2); margin-top: var(--space-3); }
  .actions button {
    flex: 1; min-height: 44px;
    border-radius: calc(var(--radius-sheet) - var(--space-5));
    font-weight: 600;
  }
  .cancel { background: var(--bg-secondary); }
  .go { background: var(--danger); color: #fff; }
</style>
```

```svelte
<!-- drishti_web/src/components/LiveView.svelte -->
<script>
  let { active } = $props();
</script>

<figure>
  {#if active}
    <img src="/api/drishti/stream" alt="Live camera view" />
  {:else}
    <div class="placeholder">Camera paused</div>
  {/if}
</figure>

<style>
  figure { margin: 0; border-radius: var(--radius-card); overflow: hidden; background: #000; }
  img { display: block; width: 100%; height: auto; }
  .placeholder {
    aspect-ratio: 4 / 3; display: grid; place-items: center;
    color: var(--label-tertiary); font-size: var(--text-footnote);
  }
</style>
```

```svelte
<!-- drishti_web/src/routes/Home.svelte -->
<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { house } from "../lib/app.svelte.js";
  import StatusCard from "../components/StatusCard.svelte";
  import LiveView from "../components/LiveView.svelte";
  import DeviceTile from "../components/DeviceTile.svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import Confirm from "../components/Confirm.svelte";

  let confirmStop = $state(false);

  onMount(() => {
    house.loadState();
    house.loadDevices();
    const timer = setInterval(() => house.loadState(), 5000);
    return () => clearInterval(timer);
  });

  async function toggle(id, action) {
    await api.post("/api/drishti/instruct", { text: `turn the ${id} ${action}` });
    await house.loadDevices();
  }
</script>

<h1>Home</h1>

<StatusCard state={house.state} />
<LiveView active={!house.state.modes?.privacy} />

<h2>Devices</h2>
{#if house.devices.length === 0}
  <EmptyState
    title="No devices yet"
    body="Add your first device in Settings, then tell the house what to do with it."
  />
{:else}
  <div class="grid">
    {#each house.devices as device (device.id)}
      <DeviceTile {device} ontoggle={toggle} />
    {/each}
  </div>
{/if}

<button class="stop" onclick={() => (confirmStop = true)}>Emergency stop</button>

<Confirm
  open={confirmStop}
  title="Stop everything?"
  body="This cuts power to every device and halts detection until you start it again."
  confirmLabel="Stop"
  onconfirm={() => (confirmStop = false)}
  oncancel={() => (confirmStop = false)}
/>

<style>
  h2 { font-size: var(--text-title-3); font-weight: 600; margin: var(--space-6) 0 var(--space-2); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space-2); }
  /* Deliberate, separated, and confirmed -- not adjacent to navigation. */
  .stop {
    margin-top: var(--space-8);
    width: 100%; min-height: 44px;
    border-radius: var(--radius-control);
    border: 1px solid var(--danger);
    color: var(--danger);
    font-weight: 600;
  }
</style>
```

Create `EmptyState.svelte` with props `{ title, body }` rendering an `<h3>` and a `<p>` inside a dashed-border panel using the same card tokens.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test tests/home.test.js`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/routes/Home.svelte drishti_web/src/components drishti_web/tests/home.test.js
git commit -m "feat(web): Home with status, live view, device tiles and a confirmed stop"
```

---

### Task 9: Rules and Activity screens

**Files:**
- Create: `drishti_web/src/routes/Rules.svelte`, `src/routes/Activity.svelte`
- Test: `drishti_web/tests/rules.test.js`, `tests/activity.test.js`

**Interfaces:**
- Consumes: `house`, `api`, `RuleCard`, `ProposalCard`, `format.relativeTime`
- Produces: `Rules` and `Activity`, neither taking props

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/rules.test.js
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import Rules from "../src/routes/Rules.svelte";

function stubRoutes(map) {
  vi.stubGlobal("fetch", (path) =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(map[path] ?? {}) }));
}

const RULE = {
  id: "r_1", source_utterance: "turn the fan off when the room is empty",
  rendered: { when: "occupancy == empty", then: "fan → off" }, enabled: true,
};

describe("rules screen", () => {
  it("shows pending proposals above saved rules", async () => {
    stubRoutes({
      "/api/drishti/rules": { rules: [RULE], orphaned: [] },
      "/api/drishti/proposals": {
        proposals: [{ id: "p1", rule: { source_utterance: "dim the lamp at ten" },
                      rendered: { when: "hour >= 22", then: "lamp → off" }, conflict: null }],
      },
      "/api/drishti/devices": { devices: [] },
    });

    render(Rules);

    await waitFor(() => expect(screen.getByText(/here's what i understood/i)).toBeInTheDocument());
    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings[0]).toHaveTextContent("dim the lamp at ten");
  });

  it("surfaces orphaned rules as needing attention", async () => {
    stubRoutes({
      "/api/drishti/rules": {
        rules: [], orphaned: [{ ...RULE, orphaned: true, enabled: false }],
      },
      "/api/drishti/proposals": { proposals: [] },
      "/api/drishti/devices": { devices: [] },
    });

    render(Rules);

    await waitFor(() => expect(screen.getByText(/needs repair/i)).toBeInTheDocument());
  });

  it("teaches on an empty rule base rather than showing a blank screen", async () => {
    stubRoutes({
      "/api/drishti/rules": { rules: [], orphaned: [] },
      "/api/drishti/proposals": { proposals: [] },
      "/api/drishti/devices": { devices: [] },
    });

    render(Rules);

    await waitFor(() =>
      expect(screen.getByText(/tell the house what to do/i)).toBeInTheDocument());
  });
});
```

```js
// drishti_web/tests/activity.test.js
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import Activity from "../src/routes/Activity.svelte";

function stub(entries) {
  vi.stubGlobal("fetch", () =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ entries }) }));
}

describe("activity screen", () => {
  it("names the device, action and the conditions that matched", async () => {
    stub([{ ts: Date.now() / 1000, device: "fan", action: "off", rule_id: "r_1",
            matched: [{ field: "occupancy", op: "==", value: "empty" }], ok: true, reason: "" }]);

    render(Activity);

    await waitFor(() => expect(screen.getByText(/fan/)).toBeInTheDocument());
    expect(screen.getByText(/occupancy == empty/)).toBeInTheDocument();
  });

  it("marks a failed actuation with its reason", async () => {
    stub([{ ts: Date.now() / 1000, device: "heater", action: "on", rule_id: "r_2",
            matched: [], ok: false, reason: "device 'heater' is unreachable" }]);

    render(Activity);

    await waitFor(() => expect(screen.getByText(/unreachable/)).toBeInTheDocument());
    expect(screen.getByText(/didn't work/i)).toBeInTheDocument();
  });

  it("says nothing has happened yet rather than showing an empty list", async () => {
    stub([]);
    render(Activity);
    await waitFor(() => expect(screen.getByText(/nothing has happened yet/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/rules.test.js tests/activity.test.js`
Expected: FAIL — cannot resolve `../src/routes/Rules.svelte`

- [ ] **Step 3: Write minimal implementation**

```svelte
<!-- drishti_web/src/routes/Rules.svelte -->
<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { house } from "../lib/app.svelte.js";
  import RuleCard from "../components/RuleCard.svelte";
  import ProposalCard from "../components/ProposalCard.svelte";
  import EmptyState from "../components/EmptyState.svelte";

  async function refresh() {
    await Promise.all([house.loadRules(), house.loadProposals(), house.loadDevices()]);
  }

  onMount(refresh);

  async function confirm(id) {
    await api.post(`/api/drishti/proposals/${id}/confirm`);
    await refresh();
  }
  async function discard(id) {
    await api.del(`/api/drishti/proposals/${id}`);
    await refresh();
  }
  async function toggle(id) {
    await api.post(`/api/drishti/rules/${id}/toggle`);
    await refresh();
  }
  async function remove(id) {
    await api.del(`/api/drishti/rules/${id}`);
    await refresh();
  }
</script>

<h1>Rules</h1>

{#each house.proposals as proposal (proposal.id)}
  <ProposalCard {proposal} onconfirm={confirm} ondiscard={discard} />
{/each}

{#if house.orphaned.length > 0}
  <h2>Needs attention</h2>
  {#each house.orphaned as rule (rule.id)}
    <RuleCard {rule} devices={house.devices} ontoggle={toggle} ondelete={remove} />
  {/each}
{/if}

{#if house.rules.length === 0 && house.proposals.length === 0 && house.orphaned.length === 0}
  <EmptyState
    title="The house hasn't been taught anything yet"
    body="Tell the house what to do using the box below — for example, “turn the lamp on when I sit at the desk”."
  />
{:else}
  {#each house.rules as rule (rule.id)}
    <RuleCard {rule} devices={house.devices} ontoggle={toggle} ondelete={remove} />
  {/each}
{/if}

<style>
  h2 { font-size: var(--text-title-3); font-weight: 600; margin: var(--space-6) 0 var(--space-2); }
  :global(article + article) { margin-top: var(--space-2); }
</style>
```

```svelte
<!-- drishti_web/src/routes/Activity.svelte -->
<script>
  import { onMount } from "svelte";
  import { house } from "../lib/app.svelte.js";
  import { relativeTime } from "../lib/format.js";
  import EmptyState from "../components/EmptyState.svelte";

  onMount(() => house.loadActivity());

  function conditions(entry) {
    return entry.matched.map((c) => `${c.field} ${c.op} ${c.value}`).join(" and ");
  }
</script>

<h1>Activity</h1>

{#if house.activity.length === 0}
  <EmptyState
    title="Nothing has happened yet"
    body="Once your rules start firing, every action shows up here with the reason behind it."
  />
{:else}
  <ol>
    {#each house.activity as entry, index (entry.ts + entry.device + index)}
      <li class:failed={!entry.ok}>
        <p class="what">
          <strong>{entry.device}</strong> {entry.ok ? "turned" : "didn't work —"} {entry.action}
        </p>
        {#if entry.matched.length > 0}
          <p class="why">{conditions(entry)}</p>
        {/if}
        {#if !entry.ok && entry.reason}
          <p class="why">{entry.reason}</p>
        {/if}
        <span class="when">{relativeTime(entry.ts)}</span>
      </li>
    {/each}
  </ol>
{/if}

<style>
  ol { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
  li {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-left: 3px solid var(--success);
    border-radius: var(--radius-card);
    padding: var(--space-3);
  }
  li.failed { border-left-color: var(--danger); }
  .what { margin: 0; }
  .why, .when { font-size: var(--text-caption-1); color: var(--label-secondary); }
  .why { margin: var(--space-1) 0 0; }
</style>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test tests/rules.test.js tests/activity.test.js`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/routes/Rules.svelte drishti_web/src/routes/Activity.svelte drishti_web/tests
git commit -m "feat(web): Rules and Activity screens"
```

---

### Task 10: Settings and add-device flow

**Files:**
- Create: `drishti_web/src/routes/Settings.svelte`, `src/routes/AddDevice.svelte`
- Test: `drishti_web/tests/adddevice.test.js`

**Interfaces:**
- Consumes: `api`, `house`, `session`
- Produces: `Settings` (no props), `AddDevice` props `{ onadded, oncancel }`

Onboarding asks four things: type, transport, name, room. Capabilities are derived from the type and never entered, and the channel list comes from the server rather than a free-text pin.

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/adddevice.test.js
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import AddDevice from "../src/routes/AddDevice.svelte";

const TYPES = {
  light: { actions: ["off", "on"], state: { kind: "enum", values: ["on", "off"] } },
  "sensor.temperature": { actions: [], state: { kind: "num", lo: -10, hi: 60 } },
};

function stubRoutes(overrides = {}) {
  vi.stubGlobal("fetch", (path, options) => {
    if (path === "/api/drishti/device-types") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ types: TYPES }) });
    }
    if (overrides.postFails) {
      return Promise.resolve({ ok: false, status: 400,
        json: () => Promise.resolve({ detail: "channel 3 is already in use" }) });
    }
    overrides.captured?.push(JSON.parse(options.body));
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
  });
}

describe("add device", () => {
  it("offers only catalogue types", async () => {
    stubRoutes();
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await waitFor(() => expect(screen.getByLabelText(/type/i)).toBeInTheDocument());
    const options = [...screen.getByLabelText(/type/i).options].map((o) => o.value);
    expect(options).toEqual(["light", "sensor.temperature"]);
  });

  it("never asks for a GPIO pin", async () => {
    stubRoutes();
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await waitFor(() => expect(screen.getByLabelText(/type/i)).toBeInTheDocument());
    expect(screen.queryByLabelText(/pin|bcm|gpio/i)).toBeNull();
    expect(screen.getByLabelText(/channel/i)).toBeInTheDocument();
  });

  it("submits the four fields and derives nothing else", async () => {
    const captured = [];
    stubRoutes({ captured });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await waitFor(() => expect(screen.getByLabelText(/type/i)).toBeInTheDocument());

    await fireEvent.change(screen.getByLabelText(/type/i), { target: { value: "light" } });
    await fireEvent.input(screen.getByLabelText(/name/i), { target: { value: "Desk lamp" } });
    await fireEvent.input(screen.getByLabelText(/room/i), { target: { value: "study" } });
    await fireEvent.click(screen.getByRole("button", { name: /add device/i }));

    await waitFor(() => expect(captured.length).toBe(1));
    expect(captured[0]).toMatchObject({ name: "Desk lamp", type: "light", room: "study" });
    expect(captured[0].transport.kind).toBe("relay");
    expect(captured[0]).not.toHaveProperty("actions");
  });

  it("shows the server's refusal verbatim", async () => {
    stubRoutes({ postFails: true });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await waitFor(() => expect(screen.getByLabelText(/type/i)).toBeInTheDocument());

    await fireEvent.input(screen.getByLabelText(/name/i), { target: { value: "Lamp" } });
    await fireEvent.click(screen.getByRole("button", { name: /add device/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("channel 3 is already in use");
  });

  it("asks for a topic instead of a channel for an MQTT device", async () => {
    stubRoutes();
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await waitFor(() => expect(screen.getByLabelText(/type/i)).toBeInTheDocument());

    await fireEvent.change(screen.getByLabelText(/connection/i), { target: { value: "mqtt" } });

    expect(screen.getByLabelText(/topic/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/channel/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/adddevice.test.js`
Expected: FAIL — cannot resolve `../src/routes/AddDevice.svelte`

- [ ] **Step 3: Write minimal implementation**

```svelte
<!-- drishti_web/src/routes/AddDevice.svelte -->
<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";

  let { onadded, oncancel } = $props();

  const CHANNELS = [1, 2, 3, 4, 5, 6, 7];

  let types = $state({});
  let type = $state("light");
  let name = $state("");
  let room = $state("");
  let kind = $state("relay");
  let channel = $state(1);
  let topicBase = $state("");
  let error = $state("");
  let busy = $state(false);

  onMount(async () => {
    const body = await api.get("/api/drishti/device-types");
    types = body.types;
    type = Object.keys(types)[0] ?? "light";
  });

  function idFrom(value) {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
      .replace(/^([^a-z])/, "d$1").slice(0, 32);
  }

  async function submit(event) {
    event.preventDefault();
    error = "";
    busy = true;
    try {
      await api.post("/api/drishti/devices", {
        id: idFrom(name),
        name: name.trim(),
        type,
        room: room.trim(),
        transport: kind === "relay"
          ? { kind: "relay", channel: Number(channel) }
          : { kind: "mqtt", topic_base: topicBase.trim() },
      });
      onadded();
    } catch (err) {
      error = err.detail ?? "Could not add the device.";
    } finally {
      busy = false;
    }
  }
</script>

<form onsubmit={submit}>
  <h2>Add a device</h2>

  <label for="type">Type</label>
  <select id="type" bind:value={type}>
    {#each Object.keys(types) as name}<option value={name}>{name}</option>{/each}
  </select>

  <label for="name">Name</label>
  <input id="name" bind:value={name} placeholder="Desk lamp" />

  <label for="room">Room</label>
  <input id="room" bind:value={room} placeholder="Study" />

  <label for="kind">Connection</label>
  <select id="kind" bind:value={kind}>
    <option value="relay">Relay channel</option>
    <option value="mqtt">Wi-Fi (MQTT)</option>
  </select>

  {#if kind === "relay"}
    <label for="channel">Channel</label>
    <select id="channel" bind:value={channel}>
      {#each CHANNELS as c}<option value={c}>{c}</option>{/each}
    </select>
  {:else}
    <label for="topic">Topic</label>
    <input id="topic" bind:value={topicBase} placeholder="drishti/heater" />
  {/if}

  {#if error}<p class="err" role="alert">{error}</p>{/if}

  <div class="actions">
    <button type="button" class="cancel" onclick={oncancel}>Cancel</button>
    <button type="submit" class="go" disabled={busy}>Add device</button>
  </div>
</form>

<style>
  form { display: grid; gap: var(--space-1); }
  h2 { font-size: var(--text-title-3); font-weight: 600; margin: 0 0 var(--space-2); }
  label { font-size: var(--text-subhead); color: var(--label-secondary); margin-top: var(--space-2); }
  input, select {
    min-height: 44px;
    padding: 0 var(--space-3);
    border-radius: var(--radius-control);
    border: 1px solid var(--separator);
    background: var(--bg-secondary);
  }
  .err { color: var(--danger); font-size: var(--text-footnote); margin: var(--space-2) 0 0; }
  .actions { display: flex; gap: var(--space-2); margin-top: var(--space-5); }
  .actions button { flex: 1; min-height: 44px; border-radius: var(--radius-control); font-weight: 600; }
  .cancel { background: var(--bg-secondary); }
  .go { background: var(--accent); color: #fff; }
</style>
```

Write `Settings.svelte` as a two-level list: Devices & Rooms (the device list plus an Add button that shows `AddDevice`, and a delete that reports how many rules were orphaned), People, Alerts, Automation and System. Gate People, Alerts, Automation and System on `session.role === "admin"` — the tab stays present for every role, only its contents change.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test`
Expected: whole suite passes

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/routes/Settings.svelte drishti_web/src/routes/AddDevice.svelte drishti_web/tests/adddevice.test.js
git commit -m "feat(web): Settings and guided add-device flow"
```

---

### Task 11: Wire the screens into the shell

**Files:**
- Modify: `drishti_web/src/App.svelte`
- Test: `drishti_web/tests/app.test.js`

**Interfaces:**
- Consumes: every route and the composer
- Produces: the finished app

- [ ] **Step 1: Write the failing test**

```js
// drishti_web/tests/app.test.js
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import App from "../src/App.svelte";
import { session } from "../src/lib/session.svelte.js";

beforeEach(() => {
  session.clear();
  vi.stubGlobal("fetch", () =>
    Promise.resolve({ ok: true, status: 200,
      json: () => Promise.resolve({ devices: [], rules: [], orphaned: [], proposals: [], entries: [] }) }));
});

describe("app", () => {
  it("shows login when signed out and no tab bar", () => {
    render(App);
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("shows the shell once signed in", async () => {
    render(App);
    session.username = "mani"; session.role = "admin"; session.signedIn = true;
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
  });

  it("shows the composer on every tab", async () => {
    render(App);
    session.signedIn = true;
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());

    for (const tab of ["Home", "Rules", "Activity", "Settings"]) {
      await fireEvent.click(screen.getByRole("tab", { name: tab }));
      expect(screen.getByLabelText(/tell the house what to do/i)).toBeInTheDocument();
    }
  });

  it("never renders a message transcript", async () => {
    render(App);
    session.signedIn = true;
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    expect(screen.queryByRole("log")).toBeNull();
    expect(document.querySelector("[class*='transcript'], [class*='messages']")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd drishti_web && npm test tests/app.test.js`
Expected: FAIL — no composer rendered

- [ ] **Step 3: Write minimal implementation**

Replace the placeholder `main` in `drishti_web/src/App.svelte`:

```svelte
<script>
  import { session } from "./lib/session.svelte.js";
  import { house } from "./lib/app.svelte.js";
  import Login from "./routes/Login.svelte";
  import Home from "./routes/Home.svelte";
  import Rules from "./routes/Rules.svelte";
  import Activity from "./routes/Activity.svelte";
  import Settings from "./routes/Settings.svelte";
  import TabBar from "./components/TabBar.svelte";
  import Composer from "./components/Composer.svelte";
  import OfflineBanner from "./components/OfflineBanner.svelte";

  let tab = $state("home");

  async function handleResult(result) {
    // A compiled proposal is a card, so send the user where cards live.
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
  <main>
    {#if tab === "home"}<Home />
    {:else if tab === "rules"}<Rules />
    {:else if tab === "activity"}<Activity />
    {:else}<Settings />{/if}
  </main>
  <Composer onresult={handleResult} />
  <TabBar current={tab} onchange={(next) => (tab = next)} />
{/if}
```

The `<style>` block from Task 5 is unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm test && npm run build`
Expected: whole suite passes; build succeeds

- [ ] **Step 5: Commit**

```bash
git add drishti_web/src/App.svelte drishti_web/tests/app.test.js
git commit -m "feat(web): wire the four screens and the composer into the shell"
```

---

### Task 12: Serve the bundle by Host header

**Files:**
- Modify: `basic_pipelines/Garuda_web.py`
- Create: `scripts/deploy_drishti.sh`
- Test: `tests/test_drishti_serving.py`

**Interfaces:**
- Consumes: `basic_pipelines/drishti_dist/`
- Produces: `/` serving the Drishti SPA on the Drishti host and the Garuda dashboard elsewhere; `/drishti/*` static assets

Both hostnames reach the same app on `localhost:8080` through the one Cloudflare tunnel, so the app decides which bundle to serve from the `Host` header. Without this, `drishti.veeramanikanta.in` shows the Garuda dashboard.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drishti_serving.py
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    from basic_pipelines.Garuda_web import app
    return TestClient(app)


def test_drishti_host_gets_the_drishti_bundle(client):
    response = client.get("/", headers={"Host": "drishti.veeramanikanta.in"})
    assert response.status_code == 200
    assert "Drishti" in response.text


def test_other_hosts_still_get_garuda(client):
    response = client.get("/", headers={"Host": "api.veeramanikanta.in"})
    assert response.status_code == 200
    assert "GARUDA" in response.text


def test_drishti_assets_are_mounted(client):
    assert client.get("/drishti/manifest.webmanifest").status_code in (200, 404)


def test_drishti_host_is_configurable(monkeypatch):
    monkeypatch.setenv("DRISHTI_HOST", "home.example.test")
    import importlib
    from basic_pipelines import Garuda_web
    importlib.reload(Garuda_web)
    assert Garuda_web.DRISHTI_HOST == "home.example.test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_drishti_serving.py -v`
Expected: FAIL — `/` returns the Garuda dashboard for both hosts

- [ ] **Step 3: Write minimal implementation**

In `basic_pipelines/Garuda_web.py`, add near the other constants:

```python
DRISHTI_HOST = os.environ.get("DRISHTI_HOST", "drishti.veeramanikanta.in")
DRISHTI_DIST = _BASE / "drishti_dist"
```

Mount the bundle next to the existing `/static` mount, and change the root handler at line 2439:

```python
if DRISHTI_DIST.is_dir():
    app.mount("/drishti", StaticFiles(directory=str(DRISHTI_DIST)), name="drishti")


@app.get("/")
async def index(request: Request):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host == DRISHTI_HOST and (DRISHTI_DIST / "index.html").is_file():
        return FileResponse(str(DRISHTI_DIST / "index.html"))
    return FileResponse(str(_static_dir / "index.html"))
```

The Drishti SPA has no client-side router — `tab` is component state, not a URL — so no catch-all rewrite is needed.

```bash
# scripts/deploy_drishti.sh
#!/bin/bash
# Build the Drishti bundle on this machine and push it to the Pi.
# The Pi has no Node toolchain and does not need one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REMOTE="${DRISHTI_REMOTE:-manikanta@ai}"
REMOTE_DIR="${DRISHTI_REMOTE_DIR:-/home/manikanta/Projects/Garuda_26}"

echo "[drishti] building..."
cd "$PROJECT_DIR/drishti_web"
npm ci
npm test
npm run build

echo "[drishti] uploading to $REMOTE..."
rsync -az --delete \
  "$PROJECT_DIR/basic_pipelines/drishti_dist/" \
  "$REMOTE:$REMOTE_DIR/basic_pipelines/drishti_dist/"

echo "[drishti] restarting server..."
ssh "$REMOTE" "cd $REMOTE_DIR && ./scripts/restart_server.sh"
echo "[drishti] done → https://drishti.veeramanikanta.in"
```

`chmod +x scripts/deploy_drishti.sh`. `DRISHTI_REMOTE` defaults to the tailnet name so the deploy works off the LAN.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd drishti_web && npm run build && cd .. && python3 -m pytest tests/test_drishti_serving.py -v`
Expected: 4 passed

- [ ] **Step 5: Run everything and commit**

```bash
python3 -m pytest tests -q
git add basic_pipelines/Garuda_web.py scripts/deploy_drishti.sh tests/test_drishti_serving.py
git commit -m "feat(web): serve the Drishti bundle by Host header, add deploy script"
```

---

## Cloudflare — one-time, on the Pi

Not a task, because it cannot be done from the Mac and it is not code. The existing `garuda-api` tunnel gains one hostname:

```bash
cloudflared tunnel route dns garuda-api drishti.veeramanikanta.in
```

Then add to `/etc/cloudflared/config.yml`, **above** the `http_status:404` catch-all:

```yaml
  - hostname: drishti.veeramanikanta.in
    service: http://localhost:8080
```

Then `sudo systemctl restart cloudflared`. Cloudflare terminates TLS, so there is no certificate to manage and the `secure=True` session cookie works.

## Verification on a real phone

The suite runs in jsdom, which has no layout engine — it cannot catch a broken safe area, an unreadable contrast ratio, or a glass panel that turns to mud over a bright camera frame. Check on the phone:

- The tab bar clears the home indicator, and the composer clears the tab bar.
- Content scrolls *under* the tab bar rather than stopping at it.
- Legible in direct sunlight, in both light and dark appearance.
- At 200% text size, no card clips its own text.
- With Reduce Transparency on, every glass surface becomes solid and stays readable.
- Installed to the home screen, it opens without browser chrome.

---

## Self-Review

**Spec coverage.** §7 four tabs → Tasks 5, 11. §7 composer docked and reachable everywhere → Tasks 6, 11. §7 rule card contents → Task 7. §7.1 Emergency Stop moved off navigation → Tasks 5, 8. §7.2 fixed structure, gated contents → Tasks 5, 10. §7.4 empty states and offline → Tasks 5, 8, 9. §6.1 confirm-before-save → Task 7. §6.2 failure surfaces including the still-working assertion → Task 6. §6.3 resolution marker → Task 6. §5.1 guided onboarding, four fields, derived capabilities → Task 10. §5.4 channel not pin → Task 10. §3 Vite + Svelte, phone first → Task 2. §9 deployment → Task 12.

**Gap found and closed during review.** `/api/state` and `/stream` authenticate on the `garuda_session` cookie, so a Drishti session reaches neither, and the Home screen needs both. Backend Task 13 did not define replacements. Task 1 adds `/api/drishti/state` and `/api/drishti/stream` and extracts the MJPEG generator so both endpoints share one implementation.

**Deferred, and stated rather than silently dropped.** The Settings sub-screens for People, Alerts, Automation and System are described in Task 10 but not written out; they are CRUD over endpoints that already exist in Garuda and need their own Drishti-auth wrappers, which is a follow-up plan. Rule *repair* for orphans surfaces the problem but does not yet offer a fix beyond delete. Emergency Stop's confirm dialog is wired but its handler is a no-op until a Drishti-auth stop endpoint exists.

**Type consistency.** `api.get/post/del` are used with those exact names in Tasks 4, 6, 8, 9, 10. `house` exposes `devices`, `rules`, `orphaned`, `proposals`, `activity`, `state`, `offline` and is read under those names in Tasks 8, 9, 11. `RuleCard` takes `{rule, devices, ontoggle, ondelete}` in both Task 7 and Task 9. `ProposalCard` takes `{proposal, onconfirm, ondiscard}` in both Task 7 and Task 9. `rendered.when` and `rendered.then` match the backend's `_render()` return shape exactly.

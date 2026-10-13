"""The real page in a real browser: handlers run, nothing inline runs.

The unit tests read the files and the API; these sign in to the running app
in Chromium and use it. They catch what only a browser shows: a handler that
throws, a script the Content-Security-Policy refuses, an on*= attribute that
crept back into generated markup, a form that reloads the page.

Needs Playwright and its Chromium (`pip install -r requirements-browser.txt`
then `python -m playwright install chromium`); without them every test here
is skipped. CI runs them in their own job. CHROMIUM_PATH points at a browser
Playwright did not install itself.
"""
import os
import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

import Garuda_web as gw  # noqa: E402  (tests/conftest.py stubs the hardware first)
import uvicorn  # noqa: E402
from basic_pipelines.garuda_auto.switch_guard import SwitchGuard  # noqa: E402

pytestmark = pytest.mark.browser

MASTER_KEY = "Browser-Test-Key-4821!"
TRICKY = "Desk lamp's \"best\" <b>x</b>"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("browser")
    mp = pytest.MonkeyPatch()
    mp.setattr(gw, "USERS_FILE", str(tmp / "users.json"))
    mp.setattr(gw, "MASTER_KEYS_FILE", str(tmp / "master_keys.json"))
    mp.setattr(gw, "REFRESH_TOKENS_FILE", str(tmp / "refresh_tokens.json"))
    mp.setattr(gw.STATE.auth, "users", {"user": {
        "password": "user", "role": "user", "display_name": "User", "box_color": "#1565c0",
        "history": {"logins": [], "narada_activity": []}}})
    mp.setattr(gw.STATE.auth, "master_keys", [])
    mp.setenv("MASTER_KEY", MASTER_KEY)
    mp.setattr(gw, "_RATE_LIMIT", 10_000)            # one browser makes many calls a second
    mp.setattr(gw, "_RATE_LIMIT_SESSION", 10_000)
    port = _free_port()
    # No lifespan: the page needs the routes, not the camera and background workers.
    srv = uvicorn.Server(uvicorn.Config(gw.fastapi_app, host="127.0.0.1", port=port,
                                        log_level="warning", lifespan="off"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)
    # Leave the test house as the rest of the suite expects it: no extra
    # device, and no switch history held against the shared lamp.
    if gw.DRISHTI_CTX.registry.delete("desk_lamp"):
        gw.DRISHTI_CTX.rebuild()
    gw.DRISHTI_CTX.device_router.guard = SwitchGuard.from_env()
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        except Exception as exc:                       # no browser on this machine
            pytest.skip(f"Chromium is not available: {exc}")
        yield b
        b.close()


class Page:
    """A signed-in page that records policy violations and page errors."""

    def __init__(self, browser, base, how):
        self.base = base
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        self.ctx.add_init_script(
            "document.addEventListener('securitypolicyviolation', e => (window.__csp = window.__csp || [])"
            ".push(e.violatedDirective + ' ' + (e.sourceFile || '') + ':' + e.lineNumber))")
        self.page = self.ctx.new_page()
        self.errors, self.requests = [], []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("request", lambda r: self.requests.append(r.method + " " + r.url.replace(base, "")))
        self.page.goto(base + "/")
        if how == "user":
            body = "{username:'user',password:'user'}"
            path = "/api/login"
        else:
            body = f"{{key:'{MASTER_KEY}'}}"
            path = "/api/master_key/login"
        status = self.js(f"fetch('{path}',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
                         f"body:JSON.stringify({body}),credentials:'include'}}).then(r => r.status)")
        assert status == 200, f"sign-in as {how} answered {status}"
        self.page.goto(base + "/")
        self.page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
        self.page.wait_for_timeout(800)

    def js(self, expr):
        return self.page.evaluate(expr)

    def go(self, page_id):
        self.js(f"document.querySelector('.ios-item[data-page={page_id}]').click()")
        self.page.wait_for_timeout(400)

    def calls_since(self, n, fragment):
        return any(fragment in r for r in self.requests[n:])

    def assert_clean(self):
        assert self.js("window.__csp || []") == []
        assert self.errors == []

    def close(self):
        self.ctx.close()


@pytest.fixture
def user_page(server, browser):
    page = Page(browser, server, "user")
    yield page
    page.close()


@pytest.fixture
def admin_page(server, browser):
    page = Page(browser, server, "admin")
    yield page
    page.close()


def test_a_household_member_uses_the_page_and_nothing_is_refused(user_page):
    p = user_page
    theme = p.js("document.documentElement.dataset.theme")
    p.page.click("#theme-toggle-btn")
    assert p.js("document.documentElement.dataset.theme") != theme      # a plain call
    p.page.click("#theme-toggle-btn")

    p.js("document.querySelector('[data-click=\"G.nav\"][data-args*=\"$q\"]').click()")
    p.page.wait_for_timeout(400)
    assert p.js("document.getElementById('page-devices').classList.contains('active')")  # a selector argument

    p.go("narada")
    url, n = p.page.url, len(p.requests)
    p.page.fill("#nx-input", "hello there")
    p.js("document.getElementById('nx-bar').requestSubmit()")
    p.page.wait_for_timeout(600)
    assert p.page.url == url and p.calls_since(n, "/api/chat")            # a form, no reload

    p.go("auto")
    n = len(p.requests)
    p.js("(() => { const i = document.querySelector('.ha-composer input, .ha-composer textarea');"
         " i.value = 'turn the lamp off when the room is empty';"
         " document.querySelector('.ha-composer').requestSubmit(); })()")
    p.page.wait_for_timeout(600)
    assert p.page.url == url and p.calls_since(n, "/api/home/instruct")
    p.assert_clean()


def test_an_admin_uses_generated_controls_and_values_stay_data(admin_page):
    p = admin_page
    status = p.js("""fetch('/api/home/devices', {method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: 'desk_lamp', name: %r, type: 'light', room: 'Study',
                              transport: {kind: 'relay', channel: 3}})}).then(r => r.status)""" % TRICKY)
    assert status in (200, 400)                     # 400: already added by an earlier run
    p.go("devices")
    p.page.wait_for_timeout(800)
    assert p.js("document.querySelectorAll('[onclick], [onchange], [oninput], [onsubmit]').length") == 0

    n = len(p.requests)
    p.page.click("[data-click='H.toggleDevice'][data-args*='desk_lamp']")
    p.page.wait_for_timeout(500)
    assert p.calls_since(n, "/api/home/devices/desk_lamp/set")            # an id and THIS

    p.js("document.querySelector('[data-click=\"H.deleteDevice\"][data-args*=\"desk_lamp\"]').click()")
    p.page.wait_for_timeout(400)
    assert TRICKY in p.js("document.getElementById('m-confirm-title').textContent")  # DATA('name'), intact
    p.page.keyboard.press("Escape")

    p.js("G.nav('settings')")
    p.page.wait_for_timeout(500)
    target = p.js("""(() => { const el = document.querySelector('[data-input="UI.showScaled"]');
        el.value = 73; el.dispatchEvent(new Event('input', {bubbles: true}));
        return JSON.parse(el.dataset.args)[1]; })()""")
    assert p.js(f"document.getElementById('{target}').textContent") == "0.73"   # this.value

    p.js("G.openDocs()")
    p.js("document.querySelector('.docs-close-btn').click()")
    assert p.js("document.getElementById('m-docs').classList.contains('hidden')")
    assert p.js("[...document.querySelectorAll('*')].filter(e => [...e.attributes]"
                ".some(a => /^on/i.test(a.name))).length") == 0
    p.assert_clean()


def test_switching_a_lamp_back_on_too_soon_is_explained_on_the_page(admin_page):
    p = admin_page
    for action in ("on", "off"):
        p.js(f"G._apiFn('POST', '/api/home/devices/lamp/set', {{action: '{action}'}})")
    reason = p.js("G._apiFn('POST', '/api/home/devices/lamp/set', {action: 'on'})"
                  ".then(() => 'switched', e => e.detail)")
    assert "protect the hardware" in reason
    p.assert_clean()


def test_an_injected_inline_script_does_not_run(server, browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(server + "/")
    page.evaluate("""document.body.insertAdjacentHTML('beforeend',
        '<img src="/nope.png" onerror="window.__pwned = 1"><a id="jl" href="javascript:window.__pwned2 = 1">x</a>')""")
    page.wait_for_timeout(500)
    page.evaluate("document.getElementById('jl').click()")
    page.wait_for_timeout(200)
    assert page.evaluate("window.__pwned === undefined && window.__pwned2 === undefined")
    ctx.close()


def test_an_admin_can_write_feedback_and_read_it_in_the_inbox(admin_page):
    # An admin used to get only the inbox: nowhere to write anything.
    for key in [k for k in gw.STATE.auth.rate_store if str(k).startswith("fb:")]:
        gw.STATE.auth.rate_store.pop(key, None)
    p = admin_page
    p.page.click("#fb-bubble")
    p.page.wait_for_timeout(400)
    assert p.page.is_visible("#fb-msg") and p.page.is_visible("#fb-tabs .fb-tab[data-tab=inbox]")
    p.page.fill("#fb-msg", "browser test: the relay clicks twice")
    p.page.click("#fb-submit")
    p.page.wait_for_selector("#fb-success.show", timeout=3000)
    p.page.wait_for_timeout(2000)                      # it closes itself after the thanks
    p.page.click("#fb-bubble")
    p.page.click("#fb-tabs .fb-tab[data-tab=inbox]")
    p.page.wait_for_timeout(600)
    assert "browser test: the relay clicks twice" in p.page.inner_text("#fb-inbox-list")
    p.page.click(".fb-close")
    assert not p.js("document.getElementById('fb-panel').classList.contains('open')")
    p.assert_clean()


def test_on_a_phone_feedback_opens_from_the_more_sheet_at_the_top(server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.wait_for_timeout(600)
    page.click(".ios-item[data-page=more]")
    page.wait_for_timeout(400)
    page.locator(".more-row", has_text="Send feedback").first.click()
    page.wait_for_timeout(600)
    # Top, so the on-screen keyboard cannot cover the box and Send.
    assert page.evaluate("document.getElementById('fb-panel').getBoundingClientRect().top") < 120
    assert page.is_visible("#fb-msg") and page.is_visible(".fb-close")
    page.click(".fb-close")
    assert not page.evaluate("document.getElementById('fb-panel').classList.contains('open')")
    ctx.close()

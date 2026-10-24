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
import json
import os
import re
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
    mp.setattr(gw, "FEEDBACK_FILE", str(tmp / "feedback.json"))
    mp.setattr(gw, "FEEDBACK_BACKUP_FILE", str(tmp / "feedback.backup.json"))
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
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
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
        # A tab when the page has one, else its row on the Settings page.
        if not self.js(f"!!document.querySelector('.ios-item[data-page={page_id}]')"):
            self.js("document.querySelector('.ios-item[data-page=settings]').click()")
            self.page.wait_for_timeout(300)
            self.js(f"document.querySelector('#settings-list .sh-row[data-page={page_id}]').click()")
        else:
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


@pytest.mark.parametrize("page_id", ["dashboard", "narada"])
def test_on_a_phone_feedback_opens_from_settings_at_the_top(server, browser, page_id):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.wait_for_timeout(600)
    page.click(f".ios-item[data-page={page_id}]")
    page.click(".ios-item[data-page=settings]")
    page.wait_for_timeout(400)
    page.locator(".sh-row", has_text="Send feedback").first.click()
    page.wait_for_timeout(600)
    # Top, so the on-screen keyboard cannot cover the box and Send.
    assert page.evaluate("document.getElementById('fb-panel').getBoundingClientRect().top") < 120
    assert page.is_visible("#fb-msg") and page.is_visible(".fb-close")
    for key in [k for k in gw.STATE.auth.rate_store if str(k).startswith("fb:")]:
        gw.STATE.auth.rate_store.pop(key, None)
    page.fill("#fb-msg", "Mobile feedback from " + page_id)
    page.click("#fb-submit")
    page.wait_for_selector("#fb-success.show")
    assert any(e["message"] == "Mobile feedback from " + page_id for e in gw._load_feedback())
    page.click(".fb-close")
    assert not page.evaluate("document.getElementById('fb-panel').classList.contains('open')")
    ctx.close()


@pytest.mark.parametrize("width,height,phone", [(1440, 900, False), (390, 844, True)])
def test_the_activity_card_is_filtered_by_its_chips(server, browser, tmp_path, monkeypatch,
                                                    width, height, phone):
    day = time.strftime("%Y-%m-%d")
    log = tmp_path / "perm_system_log.txt"
    log.write_text("".join(f"[{day} 00:0{i}:00] {text}\n" for i, text in enumerate([
        "[CAMERA] Input resolved to: rpi", "[WATCH] Person (0.83)", "Alert triggered.",
        "[NETWORK] Internet connection lost — events will be queued locally",
        "Mode dnd set to True by admin", "[OWNER] Phone arrived — device detected on network."])))
    monkeypatch.setattr(gw, "PERM_SYSTEM_LOG", str(log))
    # WebSocket state merges the in-memory log into disk history. Isolate both
    # sources from previous tests so this fixture always contains six events.
    monkeypatch.setattr(gw, "system_updates_log", [])
    ctx = browser.new_context(viewport={"width": width, "height": height},
                              is_mobile=phone, has_touch=phone)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.wait_for_timeout(600)
    page.click(".ios-item[data-page=events]")          # the timeline lives on Events
    page.wait_for_selector("#activity-timeline .timeline-item", timeout=15000)
    titles = lambda: page.locator("#activity-timeline .tl-title").all_inner_texts()
    chip = lambda name: page.locator("#activity-filters .tl-chip", has_text=name)
    def shows(n, first=""):            # not wait_for_function: the page's policy forbids eval
        for _ in range(50):
            if len(titles()) == n and titles()[0].startswith(first):
                return True
            page.wait_for_timeout(100)
        return False
    assert len(titles()) == 6 and chip("All").get_attribute("aria-pressed") == "true"

    chip("Alerts").click()
    assert shows(1, "Alert triggered.")
    chip("Warnings").click()                             # a second chip adds to the first
    assert shows(2, "[NETWORK]")
    # Every chip is on the screen or can be swiped to; the page never scrolls sideways.
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    box = page.evaluate("(() => { const f = document.getElementById('activity-filters');"
                        "return [f.scrollWidth > f.clientWidth, f.getBoundingClientRect().height,"
                        "f.querySelector('.tl-chip').getBoundingClientRect().height]; })()")
    assert box[0] == phone                                 # one swiped row on a phone
    assert box[1] < (50 if phone else 70) and box[2] >= (36 if phone else 26)

    page.reload()                                        # the choice is kept
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.wait_for_timeout(600)
    page.click(".ios-item[data-page=events]")
    page.wait_for_selector("#activity-timeline .timeline-item", timeout=15000)
    assert len(titles()) == 2 and chip("Alerts").get_attribute("aria-pressed") == "true"
    chip("Alerts").click(); chip("Warnings").click()
    chip("Presence").click(); chip("Watch").click(); chip("Presence").click()
    assert shows(1, "[WATCH]")
    chip("Watch").click()
    chip("Modes").click()
    assert shows(1, "Mode dnd")
    chip("All").click()
    assert shows(6)
    assert errors == []
    ctx.close()


def test_a_long_reply_is_two_lines_of_its_first_words_until_it_is_opened(server, browser):
    reply = "## Today\n\n" + "\n".join(f"- At {h}:00 the front door camera saw a person." for h in range(8, 20))
    sse = "".join(f"data: {json.dumps(e)}\n\n" for e in (
        [{"type": "start"}, {"type": "meta", "actions": [], "confirm": []}]
        + [{"type": "token", "text": w} for w in re.findall(r"\S+\s*", reply)] + [{"type": "done"}]))
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.route("**/api/chat/stream", lambda r: r.fulfill(
        status=200, headers={"Content-Type": "text/event-stream"}, body=sse))
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.wait_for_timeout(600)
    page.click(".ios-item[data-page=narada]")
    page.wait_for_timeout(500)
    page.fill("#nx-input", "what happened today")
    page.press("#nx-input", "Enter")
    peek = page.locator("#nx-peek")
    for _ in range(50):
        if peek.inner_text().startswith("Today • At 8:00"):
            break
        page.wait_for_timeout(100)
    assert peek.inner_text().startswith("Today • At 8:00 the front door camera saw a person.")
    # The reply does not open over the page: two lines above the bar, no more.
    assert not page.evaluate("document.getElementById('nx-dock').classList.contains('open')")
    assert page.evaluate("document.getElementById('nx-peek').getBoundingClientRect().height") <= 54
    assert page.evaluate("document.getElementById('nx-dock').getBoundingClientRect().height") < 160
    peek.click()                                         # the whole of it, when asked for
    for _ in range(100):                                 # it is typed out, a few words a frame
        if "At 19:00 the front door camera saw a person." in page.locator("#nx-log").inner_text():
            break
        page.wait_for_timeout(100)
    assert page.evaluate("document.getElementById('nx-dock').classList.contains('open')")
    assert "At 19:00 the front door camera saw a person." in page.locator("#nx-log").inner_text()
    ctx.close()


def _wait_js(page, expression):
    # Chromium's wait_for_function uses eval, which this app's CSP rejects.
    # Evaluate via the browser debugging protocol without relaxing the policy.
    for _ in range(100):
        if page.evaluate('Boolean(' + expression + ')'):
            return
        page.wait_for_timeout(100)
    details = page.evaluate("({status: document.querySelector('#nx-status')?.outerHTML, "
                            "mic: document.querySelector('#nx-mic')?.outerHTML, "
                            "callbacks: !!window.voiceCallbacks})")
    raise AssertionError(f'Browser condition did not become true: {expression}: {details}')


@pytest.mark.parametrize('mobile', [False, True])
def test_voice_replies_keep_the_same_formatting_as_typed_replies(server, browser, monkeypatch, mobile):
    import asyncio
    from types import SimpleNamespace
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice

    reply = ('## Today\n\n**Two events** recorded.\n\n1. Door opened\n2. Lamp turned on\n\n'
             'Time | Event\n--- | ---\n09:00 | Door opened\n09:15 | Lamp on\n\n'
             '▪ Check the camera\n▪ Review the logs\n\n<script>alert("unsafe")</script>')
    result = {'reply': reply, 'lane': 'agent'}
    monkeypatch.setattr(gw, '_assistant_reply', lambda *a, **kw: result)
    sdk = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(
        get_webrtc_token=lambda **kw: SimpleNamespace(token='tok', conversation_id='browser-conv'))))
    voice = NaradaVoice('test', 'engine', client=sdk,
                        reply_fn=lambda *a: {'reply': 'Two events recorded.', 'display_reply': reply})
    monkeypatch.setattr(gw, 'NARADA_VOICE', voice)
    ctx = browser.new_context(viewport={'width': 390 if mobile else 1440, 'height': 844 if mobile else 900},
                              is_mobile=mobile, has_touch=mobile, service_workers='block')
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    # Only the microphone/provider transport is simulated. Both result APIs,
    # the actual page, its event handlers and Markdown rendering run normally.
    page.route('**/static/vendor/elevenlabs-client.js', lambda r: r.fulfill(
        content_type='text/javascript', body='''
        export const Conversation = {startSession: async options => {
          window.voiceCallbacks = options;
          options.onConnect();
          return {endSession: async () => options.onDisconnect(), getId: () => 'browser-conv',
            sendUserMessage: message => options.onMessage({role: 'user', message})};
        }};'''))
    page.goto(server + '/')
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + '/')
    page.wait_for_selector('#app.logged-in', state='attached')
    page.click('.ios-item[data-page=narada]')
    page.fill('#nx-input', 'What happened today?')
    page.press('#nx-input', 'Enter')
    page.wait_for_selector('#nx-log .nx-msg.md table', state='attached')
    typed = page.locator('#nx-log .nx-msg.md').first.inner_html()
    page.click('#nx-mic')
    _wait_js(page, 'window.voiceCallbacks && document.querySelector("#nx-mic.on")')
    page.evaluate("voiceCallbacks.onMessage({role:'user',message:'Tell me again'})")
    # Fillers switch the orb to talking before the actual result exists.
    page.evaluate("voiceCallbacks.onModeChange({mode:'speaking'});"
                  "voiceCallbacks.onMessage({role:'agent',message:'One sec. A flattened transcript.'})")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, voice.reply_for('browser-conv', [
            SimpleNamespace(role='user', content='Tell me again')])).result(timeout=10)
    _wait_js(page, 'document.querySelectorAll("#nx-log .nx-msg.md table").length === 2')
    replies = page.locator('#nx-log .nx-msg.md')
    assert replies.count() == 2 and replies.nth(1).inner_html() == typed
    assert replies.nth(1).locator('ol li').count() == 2
    assert replies.nth(1).locator('ul li').count() == 2
    assert replies.nth(1).locator('script').count() == 0
    page.evaluate('N.toggleSheet(true)')
    assert page.locator('#nx-log .nx-msg.md table').last.is_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Text sent during a call must appear once even when the SDK echoes it.
    page.fill('#nx-input', 'One more question')
    page.press('#nx-input', 'Enter')
    assert page.locator('#nx-log .nx-msg.you', has_text='One more question').count() == 1
    # Expired bindings must stop polling and close the voice connection.
    with voice._lock:
        voice._bindings.clear()
    _wait_js(page, '!N.voiceActive()')
    assert 'Voice session expired' in page.locator('#nx-status').get_attribute('data-err')
    page.reload()
    page.wait_for_selector('#app.logged-in', state='attached')
    page.click('.ios-item[data-page=narada]')
    page.evaluate('N.toggleSheet(true)')
    assert page.locator('#nx-log .nx-msg.md').count() == 2
    assert page.locator('#nx-log .nx-msg.md').last.inner_html() == typed
    if mobile:
        page.screenshot(path='/tmp/garuda-narada-mobile.png')
    assert errors == []
    ctx.close()


def test_cancelled_voice_start_cannot_reopen_the_microphone(user_page):
    p = user_page
    p.page.route('**/api/narada/voice/token', lambda r: r.fulfill(
        content_type='application/json', body='{"token":"tok","conversation_id":"race"}'))
    p.page.route('**/static/vendor/elevenlabs-client.js', lambda r: r.fulfill(
        content_type='text/javascript', body='''
        export const Conversation = {startSession: options => new Promise(resolve => {
          window.finishVoiceStart = () => {
            options.onConnect();
            resolve({endSession: async () => {window.voiceEnded = true; options.onDisconnect();}});
          };
        })};'''))
    p.go('narada')
    p.page.click('#nx-mic')
    _wait_js(p.page, 'window.finishVoiceStart')
    p.page.click('#nx-mic')
    p.js('finishVoiceStart()')
    _wait_js(p.page, 'window.voiceEnded')
    assert not p.js('N.voiceActive()')
    assert not p.page.locator('#nx-mic').get_attribute('aria-pressed') == 'true'
    p.assert_clean()


@pytest.mark.parametrize("network_error", [False, True])
def test_an_interrupted_chat_stream_is_not_shown_as_a_complete_answer(user_page, network_error):
    p = user_page
    partial = ''.join('data: ' + json.dumps(e) + '\n\n' for e in [
        {'type': 'start'}, {'type': 'meta', 'actions': []},
        {'type': 'token', 'text': 'I changed the'}])
    p.page.route('**/api/chat/stream', lambda r: r.abort() if network_error else r.fulfill(
        content_type='text/event-stream', body=partial))
    p.go('narada')
    p.page.fill('#nx-input', 'Change the light')
    p.page.press('#nx-input', 'Enter')
    p.page.wait_for_selector('#nx-log .error', state='attached')
    assert ('Connection lost' if network_error else 'interrupted') in p.page.locator('#nx-log .error').text_content()
    assert not any(r.endswith('/api/chat') for r in p.requests)
    assert p.page.locator('#nx-log .narada.md').count() == 0
    p.assert_clean()


def test_the_feedback_form_keeps_its_spacing_after_a_send(server, browser):
    # The reset after a send cleared the form's inline display:flex, and the
    # rows lost their 14 px gaps from the second feedback on.
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                              service_workers="block")
    page = ctx.new_page()
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.wait_for_timeout(600)
    layout = ("(() => { const b = document.getElementById('fb-form-body'), s = getComputedStyle(b);"
              " const kids = [...b.children].filter(k => k.offsetParent);"
              " return {display: s.display, gap: s.rowGap,"
              " space: kids[1].getBoundingClientRect().top - kids[0].getBoundingClientRect().bottom}; })()")

    def open_form():
        page.click(".ios-item[data-page=settings]")
        page.wait_for_timeout(400)
        page.locator(".sh-row", has_text="Send feedback").first.click()
        page.wait_for_timeout(500)
    open_form()
    before = page.evaluate(layout)
    assert before["display"] == "flex" and before["gap"] == "14px" and before["space"] >= 13
    for key in [k for k in gw.STATE.auth.rate_store if str(k).startswith("fb:")]:
        gw.STATE.auth.rate_store.pop(key, None)
    page.fill("#fb-msg", "spacing check")
    page.click("#fb-submit")
    page.wait_for_selector("#fb-success.show")
    page.wait_for_timeout(2200)             # the panel closes itself after the thanks
    open_form()
    assert page.evaluate(layout) == before
    ctx.close()


def test_a_typed_answer_shows_as_it_is_written_and_is_replaced_in_place(server, browser, monkeypatch):
    import time as _time

    def fake(msg, user, role, scope, progress=None, on_draft=None, **kw):
        on_draft("Let me check. ")
        _time.sleep(0.4)
        on_draft(None)                      # that round went to a tool
        on_draft("Two events today:\n\n1. **Door** opened\n")
        _time.sleep(1.2)
        on_draft("2. Lamp on\n")
        return {"reply": "Two events today:\n\n1. **Door** opened\n2. Lamp on", "lane": "agent",
                "actions": []}
    monkeypatch.setattr(gw, "_assistant_reply", fake)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                              service_workers="block")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.click(".ios-item[data-page=narada]")
    page.evaluate("N.toggleSheet(true)")
    page.fill("#nx-input", "What happened today?")
    page.press("#nx-input", "Enter")
    # The draft is on screen while the answer is still being written.
    _wait_js(page, 'document.querySelector("#nx-log .nx-msg.draft") && '
                   'document.querySelector("#nx-log .nx-msg.draft").textContent.includes("Door opened")')
    assert "Let me check" not in page.locator("#nx-log .nx-msg.draft").text_content()
    assert page.locator("#nx-log .nx-msg.md").count() == 0
    page.wait_for_selector("#nx-log .nx-msg.md ol li >> nth=1", state="attached", timeout=8000)
    narada = page.locator("#nx-log .nx-msg.narada:not(.pending)")
    assert narada.count() == 1                       # replaced in place, not added
    assert page.locator("#nx-log .nx-msg.draft").count() == 0
    assert page.locator("#nx-log .nx-msg.md b", has_text="Door").count() == 1
    assert errors == []
    ctx.close()


def test_an_interrupted_stream_leaves_no_half_written_draft(user_page):
    p = user_page
    partial = ''.join('data: ' + json.dumps(e) + '\n\n' for e in [
        {'type': 'start'}, {'type': 'draft', 'text': 'I turned the lamp'}])
    p.page.route('**/api/chat/stream', lambda r: r.fulfill(content_type='text/event-stream', body=partial))
    p.go('narada')
    p.page.fill('#nx-input', 'Lamp on')
    p.page.press('#nx-input', 'Enter')
    p.page.wait_for_selector('#nx-log .error', state='attached')
    assert p.page.locator('#nx-log .nx-msg.narada:not(.error):not(.pending)').count() == 0
    p.assert_clean()


def test_a_spoken_summary_shows_at_once_and_the_full_answer_replaces_it(server, browser, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice

    full = "Two alerts today, both at the front.\n\n1. **Tamper** at 04:11\n2. Knife at 20:07"
    ids = iter(['sum-conv'] + ['sum-conv-next-%d' % n for n in range(20)])   # a new id per token, as ElevenLabs does
    sdk = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(
        get_webrtc_token=lambda **kw: SimpleNamespace(token='tok', conversation_id=next(ids)))))
    voice = NaradaVoice('test', 'engine', client=sdk, reply_fn=lambda *a: {
        'reply': 'Two alerts today, both at the front. The rest is on your screen.',
        'display_reply': full})
    monkeypatch.setattr(gw, 'NARADA_VOICE', voice)
    ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True,
                              service_workers='block')
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/static/vendor/elevenlabs-client.js', lambda r: r.fulfill(
        content_type='text/javascript', body='''
        export const Conversation = {startSession: async options => {
          window.voiceCallbacks = options;
          options.onConnect();
          return {endSession: async () => options.onDisconnect(), getId: () => 'sum-conv',
            sendUserMessage: message => options.onMessage({role: 'user', message})};
        }};'''))
    page.goto(server + '/')
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + '/')
    page.wait_for_selector('#app.logged-in', state='attached')
    page.click('.ios-item[data-page=narada]')
    page.evaluate('N.toggleSheet(true)')

    def answer(question):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, voice.reply_for('sum-conv', [
                SimpleNamespace(role='user', content=question)])).result(timeout=10)

    page.click('#nx-mic')
    _wait_js(page, 'window.voiceCallbacks && document.querySelector("#nx-mic.on")')
    page.evaluate("voiceCallbacks.onMessage({role:'user',message:'Any alerts?'})")
    page.evaluate("voiceCallbacks.onMessage({role:'agent',message:'Two alerts today, both at the front. "
                  "The rest is on your screen.'})")
    _wait_js(page, 'document.querySelector("#nx-log .nx-msg.draft")')
    assert 'Two alerts today' in page.locator('#nx-log .nx-msg.draft').text_content()
    answer('Any alerts?')
    _wait_js(page, 'document.querySelector("#nx-log .nx-msg.md ol")')
    assert page.locator('#nx-log .nx-msg.narada:not(.pending)').count() == 1
    assert page.locator('#nx-log .nx-msg.draft').count() == 0
    assert 'rest is on your screen' not in page.locator('#nx-log .nx-msg.md').text_content()

    # Hung up after the spoken part: the written answer still arrives.
    page.evaluate("voiceCallbacks.onMessage({role:'user',message:'And yesterday?'})")
    page.evaluate("voiceCallbacks.onMessage({role:'agent',message:'Two alerts today, both at the front.'})")
    page.evaluate('N.stopVoice()')                # ends the call (the mic is closed: a tap would open it)
    _wait_js(page, '!N.voiceActive()')
    answer('And yesterday?')
    _wait_js(page, 'document.querySelectorAll("#nx-log .nx-msg.md ol").length === 2')
    assert page.locator('#nx-log .nx-msg.narada:not(.pending)').count() == 2
    assert errors == []
    ctx.close()


def test_a_waiting_card_comes_back_after_a_reload_and_the_closed_bar_says_so(server, browser):
    # The owner asked for a shortcut by voice, closed and opened the chat, and
    # never saw a Confirm button: the card lived only in the page.
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                              service_workers="block")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    card = gw.AGENT.confirmations.add(gw.AGENT_CAPABILITIES["add_tracked_phone"],
                                      {"name": "Asha's phone", "mac": "aa:bb:cc:dd:ee:01"}, "user")
    try:
        page.click(".ios-item[data-page=narada]")
        sel = f'#nx-log .nx-card[data-action="{card["id"]}"]'
        page.wait_for_selector(sel, state="attached", timeout=5000)
        page.reload()
        page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
        page.click(".ios-item[data-page=narada]")
        page.wait_for_selector(sel, state="attached", timeout=5000)
        assert page.locator(sel).count() == 1
        page.evaluate("N.toggleSheet(false)")
        assert page.locator("#nx-peek").text_content().startswith("Needs your confirmation: Add tracked phone")
        page.evaluate("N.toggleSheet(true)")
        page.wait_for_timeout(400)
        assert page.locator(sel).count() == 1           # opened again: not drawn twice
        page.locator(sel + " [data-card=cancel]").click()
        page.wait_for_selector(sel + ".closed", state="attached", timeout=5000)
        page.evaluate("N.toggleSheet(false)")
        assert "Needs your confirmation" not in (page.locator("#nx-peek").text_content() or "")
        assert errors == []
    finally:
        gw.AGENT.confirmations.take(card["id"], "user")
        ctx.close()


def test_the_microphone_closes_once_a_question_is_in_and_opens_only_on_a_tap(server, browser, monkeypatch):
    # The owner: once my question is captured, stop listening, so voices in
    # the room cannot cut the answer; listen again only when I tap.
    from types import SimpleNamespace
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice

    sdk = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(
        get_webrtc_token=lambda **kw: SimpleNamespace(token='tok', conversation_id='mic-conv'))))
    voice = NaradaVoice('test', 'engine', client=sdk, reply_fn=lambda *a: {'reply': 'Quiet.'})
    monkeypatch.setattr(gw, 'NARADA_VOICE', voice)
    ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True,
                              service_workers='block')
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/static/vendor/elevenlabs-client.js', lambda r: r.fulfill(
        content_type='text/javascript', body='''
        export const Conversation = {startSession: async options => {
          window.voiceCallbacks = options; window.micLog = []; window.ended = 0;
          options.onConnect();
          return {endSession: async () => { window.ended++; options.onDisconnect(); },
            setMicMuted: m => window.micLog.push(m), getId: () => 'mic-conv',
            sendUserMessage: message => options.onMessage({role: 'user', message})};
        }};'''))
    page.goto(server + '/')
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + '/')
    page.wait_for_selector('#app.logged-in', state='attached')
    page.click('.ios-item[data-page=narada]')
    page.evaluate('N.toggleSheet(true)')
    page.click('#nx-mic')
    _wait_js(page, 'window.voiceCallbacks && document.querySelector("#nx-mic.on")')
    assert page.evaluate('window.micLog') == []                     # listening for the question
    # The server hears the question: shown at once, microphone closed.
    with voice._lock:
        voice._heard['mic-conv'] = {'n': 1, 'text': 'Any alerts today?'}
    _wait_js(page, 'window.micLog.length === 1')
    assert page.evaluate('window.micLog') == [True]
    assert page.locator('#nx-log .nx-msg.you', has_text='Any alerts today?').count() == 1
    # The speech service's own copy of the turn, seconds later, is the same turn.
    page.evaluate("voiceCallbacks.onMessage({role:'user',message:'Any alerts today?'})")
    assert page.locator('#nx-log .nx-msg.you', has_text='Any alerts today?').count() == 1
    # Narada answers and finishes: the microphone stays closed.
    with voice._lock:
        voice._turns['mic-conv'].append({'id': 1, 'heard': 'Any alerts today?', 'response': 'None today.'})
    page.evaluate("voiceCallbacks.onModeChange({mode:'speaking'})")
    _wait_js(page, 'document.querySelector("#nx-log .nx-msg.md")')
    page.evaluate("voiceCallbacks.onModeChange({mode:'listening'})")
    page.wait_for_timeout(700)
    assert page.evaluate('window.micLog') == [True]
    assert 'Tap the mic to talk' in page.locator('#nx-status').text_content()
    assert page.locator('#nx-mic.held').count() == 1
    # A tap opens it, and the call goes on.
    page.click('#nx-mic')
    assert page.evaluate('window.micLog') == [True, False] and page.evaluate('window.ended') == 0
    assert page.locator('#nx-status').text_content() == 'Listening'
    # Holding the button ends the call.
    box = page.locator('#nx-mic').bounding_box()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    page.mouse.down()
    page.wait_for_timeout(800)
    page.mouse.up()
    _wait_js(page, '!N.voiceActive()')
    assert page.evaluate('window.ended') == 1
    page.wait_for_timeout(300)
    assert not page.evaluate('N.voiceActive()')                    # the release did not start a new call
    assert errors == []
    ctx.close()


def test_a_call_starts_on_a_token_fetched_before_the_tap_and_a_quick_restart_skips_the_greeting(
        server, browser, monkeypatch):
    # Measured 2026-10-15 against ElevenLabs: tap to connected 3.8-4.2 s with
    # the token fetched on the tap, 3.1-3.4 s with it fetched beforehand.
    from types import SimpleNamespace
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice

    issued = []

    def token(**kw):
        issued.append(kw)
        return SimpleNamespace(token='tok%d' % len(issued), conversation_id='pre-conv-%d' % len(issued))
    sdk = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(get_webrtc_token=token)))
    monkeypatch.setattr(gw, 'NARADA_VOICE', NaradaVoice('test', 'engine', client=sdk,
                                                        reply_fn=lambda *a: {'reply': 'ok'}))
    ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True,
                              service_workers='block')
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/static/vendor/elevenlabs-client.js', lambda r: r.fulfill(
        content_type='text/javascript', body='''
        export const Conversation = {startSession: async options => {
          (window.calls = window.calls || []).push({token: options.conversationToken,
                                                    greeting: options.overrides.agent.firstMessage});
          window.voiceCallbacks = options;
          options.onConnect();
          return {endSession: async () => options.onDisconnect(), setMicMuted: () => {},
                  getId: () => 'x', sendUserMessage: () => {}};
        }};'''))
    page.goto(server + '/')
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + '/')
    page.wait_for_selector('#app.logged-in', state='attached')
    page.evaluate("sessionStorage.removeItem('narada-call-ended')")
    page.click('.ios-item[data-page=narada]')
    page.wait_for_timeout(1500)
    assert len(issued) == 1                                 # fetched when the page opened
    page.click('#nx-mic')
    _wait_js(page, 'window.calls && window.calls.length === 1')
    assert len(issued) == 1                                 # the tap used it: no request on the tap
    first = page.evaluate('window.calls[0]')
    assert first['token'] == 'tok1' and first['greeting']   # a first call is greeted
    page.evaluate('N.stopVoice()')
    page.wait_for_timeout(1200)
    assert len(issued) == 2                                 # the next call's token, after the call ended
    page.click('#nx-mic')
    _wait_js(page, 'window.calls.length === 2')
    again = page.evaluate('window.calls[1]')
    assert again == {'token': 'tok2', 'greeting': ''}       # straight back to listening
    assert len(issued) == 2
    assert errors == []
    ctx.close()


def test_the_voice_shows_in_the_text_field_only_while_the_microphone_listens(server, browser, monkeypatch):
    from types import SimpleNamespace
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice

    sdk = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(
        get_webrtc_token=lambda **kw: SimpleNamespace(token='tok', conversation_id='wave-conv'))))
    voice = NaradaVoice('test', 'engine', client=sdk, reply_fn=lambda *a: {'reply': 'ok'})
    monkeypatch.setattr(gw, 'NARADA_VOICE', voice)
    ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True,
                              service_workers='block')
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/static/vendor/elevenlabs-client.js', lambda r: r.fulfill(
        content_type='text/javascript', body='''
        export const Conversation = {startSession: async options => {
          window.voiceCallbacks = options; window.level = 0.25;
          options.onConnect();
          return {endSession: async () => options.onDisconnect(), setMicMuted: () => {},
                  getInputVolume: () => window.level, getId: () => 'wave-conv', sendUserMessage: () => {},
                  // The field reads the client's frequency bins (b703bfa), not its volume.
                  getInputByteFrequencyData: () => new Uint8Array(1024).fill(Math.round(window.level * 255))};
        }};'''))
    page.goto(server + '/')
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + '/')
    page.wait_for_selector('#app.logged-in', state='attached')
    page.click('.ios-item[data-page=narada]')
    # Lit pixels in the newest fifth of the field (the right end): bars roll leftwards.
    lit = ("(() => { const c = document.getElementById('nx-wave'); if (!c.width) return 0;"
           " const x = Math.floor(c.width * 0.8);"
           " const d = c.getContext('2d').getImageData(x, 0, c.width - x, c.height).data; let n = 0;"
           " for (let i = 3; i < d.length; i += 4) if (d[i] > 20) n++; return n; })()")
    assert not page.locator('#nx-bar.listening').count()
    page.click('#nx-mic')
    _wait_js(page, 'document.querySelector("#nx-bar.listening")')
    page.wait_for_timeout(900)
    loud = page.evaluate(lit)
    assert loud > 60                                           # bars drawn from the voice
    page.evaluate("window.level = 0.12")
    page.wait_for_timeout(1200)
    page.locator('#nx-bar').screenshot(path='/tmp/garuda-narada-wave.png')
    assert page.evaluate("getComputedStyle(document.getElementById('nx-wave')).opacity") == '1'
    page.evaluate('window.level = 0')
    page.wait_for_timeout(1500)
    assert page.evaluate(lit) < loud / 3                       # quiet: back to small dots
    # Typing hides it.
    page.fill('#nx-input', 'hello')
    page.wait_for_timeout(900)                                 # the 0.35 s fade, with room
    assert page.evaluate("getComputedStyle(document.getElementById('nx-wave')).opacity") == '0'
    page.fill('#nx-input', '')
    # The question is in: the microphone closes and the voice goes from the field.
    with voice._lock:
        voice._heard['wave-conv'] = {'n': 1, 'text': 'Is the lamp on?'}
    _wait_js(page, '!document.querySelector("#nx-bar.listening")')
    assert errors == []
    ctx.close()


def test_the_voice_log_scrolls_reads_clean_and_keeps_up_while_open(admin_page):
    # The owner: the Voice section of the Logs page was stuck, not scrollable,
    # and did not update.
    import datetime as _dt
    p = admin_page
    now = _dt.datetime.now()
    os.makedirs(os.path.dirname(gw.PERM_VOICE_LOG), exist_ok=True)
    with open(gw.PERM_VOICE_LOG, "w", encoding="utf-8") as f:
        for n in range(80):
            stamp = (now - _dt.timedelta(minutes=90 - n)).strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{stamp}] You said: question {n}\n→ [{stamp}] answer {n}, no oneâ\x80\x99s home\n")
    p.go('a-logs')
    p.page.click('.log-tab[data-log=voice]')
    p.page.wait_for_selector('#a-vlog .log-line', state='attached', timeout=8000)
    pane = "document.getElementById('a-vlog')"
    assert p.js(f"getComputedStyle({pane}).overflowY") == 'auto'
    assert p.js(f"{pane}.scrollHeight > {pane}.clientHeight + 100")
    p.js(f"{pane}.scrollTop = 0")
    assert p.js(f"{pane}.scrollTop") == 0                       # it moves: scrollable
    assert "no one’s home" in p.page.locator('#a-vlog').text_content()
    assert "â" not in p.page.locator('#a-vlog').text_content()
    # A turn spoken while the page is open shows up without a reload.
    gw.append_voice_log("You said: brand new question")
    p.page.click('.log-tab[data-log=system]')
    p.page.click('.log-tab[data-log=voice]')
    for _ in range(80):
        if "brand new question" in (p.page.locator('#a-vlog').text_content() or ""):
            break
        p.page.wait_for_timeout(100)
    assert "brand new question" in p.page.locator('#a-vlog').text_content()
    gw.append_voice_log("You said: and one more, on the timer")
    for _ in range(140):
        if "on the timer" in (p.page.locator('#a-vlog').text_content() or ""):
            break
        p.page.wait_for_timeout(100)
    assert "on the timer" in p.page.locator('#a-vlog').text_content()
    p.assert_clean()


def test_captures_of_one_alert_show_together_and_play(admin_page, tmp_path, monkeypatch):
    """A shortcut's picture, sound and video of one alert: one card, each one plays."""
    import cv2
    import numpy as np
    from basic_pipelines.garuda_core import audio_feed, captures
    store = captures.CaptureStore(str(tmp_path / "captures"))
    monkeypatch.setattr(gw, "CAPTURES", store)
    frame = np.zeros((120, 160, 3), np.uint8)
    frame[:, :80] = (40, 120, 220)
    jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
    for kind, data in (("snapshot", jpeg),
                       ("audio", audio_feed.wav_bytes(b"\x00\x01" * 16000)), ("video", None)):
        entry = store.begin(kind, by="admin", label="Alert evidence", group="run1",
                            alert="knife (0.91)", seconds=None if kind == "snapshot" else 2)
        if kind == "video":
            assert captures.encode_video([jpeg] * 20, 10, store.path_of(entry))
        store.finish(entry["id"], data)
    p = admin_page
    p.go("events")
    p.page.wait_for_selector(".ha-cap-group img", timeout=5000)
    def until(expr, seconds):           # not wait_for_function: the page's policy forbids eval
        for _ in range(int(seconds * 10)):
            if p.js(expr):
                return
            p.page.wait_for_timeout(100)
        assert p.js(expr), expr
    until("document.querySelector('.ha-cap-img').naturalWidth > 0", 5)
    until("document.querySelector('.ha-cap-video').readyState >= 1", 8)
    assert p.js("document.querySelectorAll('.ha-cap-group').length") == 1
    assert p.js("document.querySelectorAll('.ha-cap-group audio').length") == 1
    assert "knife" in p.js("document.querySelector('.ha-cap-group').textContent")
    kept = next(c for c in store.list() if c["kind"] == "snapshot")
    p.js("document.querySelector('.ha-cap-item [data-click=\"H.keepCapture\"]').click()")
    p.page.wait_for_timeout(600)
    assert store.get(kept["id"])["keep"] is True
    p.assert_clean()


def _pill_center(page):
    return page.evaluate("(() => { const r = document.getElementById('ios-pill').getBoundingClientRect();"
                         " return [r.left + r.width / 2, r.top + r.height / 2]; })()")


def _tab_center(page, page_id):
    return page.evaluate(f"(() => {{ const r = document.querySelector('.ios-item[data-page={page_id}]')"
                         f".getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")


def _active(page, page_id):
    return page.evaluate(f"document.getElementById('page-{page_id}').classList.contains('active')")


def test_the_nav_pill_is_dragged_to_a_tab_and_opens_it(user_page):
    p = user_page
    page = p.page
    p.go("dashboard")
    page.wait_for_timeout(600)
    x, y = _pill_center(page)
    tx, _ = _tab_center(page, "events")
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, 21):                            # a slow drag: no throw
        page.mouse.move(x + (tx - x) * i / 20, y)
        page.wait_for_timeout(16)
    page.wait_for_timeout(200)
    assert p.js("document.getElementById('ios-nav').classList.contains('ios-nav-dragging')")
    page.mouse.up()
    page.wait_for_timeout(900)
    assert _active(page, "events") and not _active(page, "dashboard")
    assert abs(_pill_center(page)[0] - tx) < 2         # settled under the tab
    assert not p.js("document.getElementById('ios-nav').classList.contains('ios-nav-dragging')")

    # Let go short of a tab: it snaps to the nearest one.
    dx, _ = _tab_center(page, "devices")
    x, y = _pill_center(page)
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, 16):
        page.mouse.move(x + (dx + 12 - x) * i / 15, y)
        page.wait_for_timeout(16)
    page.wait_for_timeout(200)
    page.mouse.up()
    page.wait_for_timeout(900)
    assert _active(page, "devices")
    assert abs(_pill_center(page)[0] - dx) < 2

    # A tap still works, and the click after a drag was not taken as one.
    p.go("narada")
    assert _active(page, "narada")
    page.wait_for_timeout(600)
    assert abs(_pill_center(page)[0] - _tab_center(page, "narada")[0]) < 2
    p.assert_clean()


def test_emergency_stop_is_not_a_tab_and_asks_first(admin_page):
    """One tap in the tab bar used to stop the whole system. It now lives on
    System, behind the confirmation."""
    p = admin_page
    page = p.page
    assert p.js("document.querySelectorAll('#ios-nav .ios-item').length") == 5
    assert p.js("document.querySelector('#ios-nav .ios-danger')") is None
    p.js("window.__stops = 0; const f = window.fetch; window.fetch = (u, o) =>"
         " { if (String(u).includes('emergency')) window.__stops++; return f(u, o); }")
    p.go("a-settings")
    assert p.js("document.querySelector('.ios-item[data-page=settings]').classList.contains('active')")
    page.click(".danger-zone .btn-danger")
    page.wait_for_timeout(300)
    page.click("#m-confirm-cancel")
    page.wait_for_timeout(300)
    assert p.js("window.__stops") == 0
    p.js("document.querySelector('#page-a-settings .sh-back').click()")
    page.wait_for_timeout(300)
    assert _active(page, "settings")
    p.assert_clean()


@pytest.mark.parametrize("reduced", [False, True])
def test_on_a_phone_the_nav_pill_is_swiped_with_a_finger(server, browser, reduced):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                              reduced_motion="reduce" if reduced else "no-preference")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/")
    page.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:'user',password:'user'}),credentials:'include'})")
    page.goto(server + "/")
    page.wait_for_selector("#app.logged-in", state="attached", timeout=15000)
    page.wait_for_timeout(600)
    cdp = ctx.new_cdp_session(page)
    x, y = _pill_center(page)
    tx, _ = _tab_center(page, "narada")
    touch = lambda kind, px: cdp.send("Input.dispatchTouchEvent", {
        "type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": px, "y": y}]})
    touch("touchStart", x)
    for i in range(1, 21):
        touch("touchMove", x + (tx - x) * i / 20)
        page.wait_for_timeout(16)
    page.wait_for_timeout(200)
    touch("touchEnd", tx)
    page.wait_for_timeout(50 if reduced else 900)
    assert _active(page, "narada")
    assert abs(_pill_center(page)[0] - tx) < 2
    assert errors == []
    ctx.close()


def test_a_quick_flick_throws_the_nav_pill_past_the_tab_under_the_hand(user_page):
    p = user_page
    page = p.page
    p.go("dashboard")
    page.wait_for_timeout(600)
    x, y = _pill_center(page)
    near, _ = _tab_center(page, "devices")
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, 6):                              # fast, and let go while still moving
        page.mouse.move(x + (near - x) * i / 5, y)
    page.mouse.up()
    page.wait_for_timeout(900)
    assert _active(page, "narada") or _active(page, "settings")
    p.assert_clean()


def _pill_scale(page):
    return page.evaluate("(() => { const m = new DOMMatrix(getComputedStyle(document.getElementById('ios-pill'))"
                         ".transform); return [m.a, m.d]; })()")


def test_the_nav_pill_bulges_while_it_is_pressed(user_page):
    p = user_page
    page = p.page
    p.go("dashboard")
    page.wait_for_timeout(600)
    assert _pill_scale(page) == [1, 1]
    x, y = _pill_center(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(300)
    sx, sy = _pill_scale(page)
    assert sx > 1.05 and sy > 1.1                       # swollen under the press, taller than wide
    assert p.js("document.getElementById('ios-pill').classList.contains('ios-pill-lifted')")
    page.mouse.up()
    page.wait_for_timeout(900)
    assert _pill_scale(page) == [1, 1]
    assert not p.js("document.getElementById('ios-pill').classList.contains('ios-pill-lifted')")

    # Even a quick tap shows the bulge before the pill settles back.
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(80)
    assert _pill_scale(page)[1] > 1.03
    page.wait_for_timeout(900)
    assert _pill_scale(page) == [1, 1]
    assert _active(page, "dashboard")
    p.assert_clean()

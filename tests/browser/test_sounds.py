"""The interface sounds, heard in a real browser: each control plays its sound.

sound.js decides which control plays what; these tests click the real page
and record every sound that starts (by the file it was decoded from) and
every haptic asked for. PW_ENGINE=webkit runs them in Safari's engine, which
is where the owner listens; CI runs Chromium.
"""
import os
import time

import pytest

from test_pages import Page, playwright_api, server  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser

# Every sound started is recorded as [file name, context state]: the decoded
# buffer is traced back to the response it came from.
RECORD = """(() => {
  const urlOf = new WeakMap(), nameOf = new WeakMap();
  window.__sounds = []; window.__haptics = [];
  const ab = Response.prototype.arrayBuffer;
  Response.prototype.arrayBuffer = function () {
    const u = this.url;
    return ab.call(this).then(b => { urlOf.set(b, u); return b; });
  };
  const dec = BaseAudioContext.prototype.decodeAudioData;
  BaseAudioContext.prototype.decodeAudioData = function (data, ok, no) {
    const m = (urlOf.get(data) || '').match(/sounds\\/([\\w-]+)\\.mp3/);
    const tag = b => { if (m) nameOf.set(b, m[1]); return b; };
    const p = dec.call(this, data, b => { tag(b); if (ok) ok(b); }, no);
    return p && p.then ? p.then(tag) : p;
  };
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...a) {
    const n = this.buffer && nameOf.get(this.buffer);
    if (n) window.__sounds.push(n);
    return start.apply(this, a);
  };
  if (navigator.vibrate) {
    const vib = navigator.vibrate.bind(navigator);
    navigator.vibrate = p => { window.__haptics.push(p); return vib(p); };
  }
})()"""


@pytest.fixture(scope="module")
def browser():
    engine = os.environ.get("PW_ENGINE", "chromium")
    with playwright_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch(
                executable_path=(os.environ.get("CHROMIUM_PATH") if engine == "chromium" else None) or None)
        except Exception as exc:
            pytest.skip(f"{engine} is not available: {exc}")
        yield b
        b.close()


class Heard:
    def __init__(self, p):
        self.p, self.n = p, 0

    def after(self, act, wait=500):
        """The sounds the act started."""
        act()
        self.p.page.wait_for_timeout(wait)
        played = self.p.js("window.__sounds")
        new, self.n = played[self.n:], len(played)
        return new


@pytest.fixture
def admin(server, browser):  # noqa: F811
    real = browser.new_context
    browser.new_context = lambda **kw: _recording(real(**kw))
    try:
        page = Page(browser, server, "admin")
    finally:
        browser.new_context = real
    page.page.wait_for_timeout(1800)              # sound.js decodes after the page settles
    yield page
    page.close()


def _recording(ctx):
    ctx.add_init_script(RECORD)
    return ctx


def test_every_sound_decodes_in_this_browser(admin):
    names = admin.js("""Promise.all(['on','off','lock','unlock','delete','shutter','send','receive','success',
        'warning','error','rec-start','rec-stop','voice-start','voice-end','alert'].map(n =>
        fetch('/static/sounds/' + n + '.mp3').then(r => r.arrayBuffer())
          .then(b => new AudioContext().decodeAudioData(b)).then(() => n, () => 'BROKEN ' + n)))""")
    assert not [n for n in names if n.startswith("BROKEN")]


def test_controls_play_their_sound_and_small_moves_stay_silent(admin):
    p, h = admin, Heard(admin)
    p.js("""fetch('/api/home/devices', {method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: 'desk_lamp', name: 'Desk lamp', type: 'light', room: 'Study',
                              transport: {kind: 'relay', channel: 3}})})""")

    assert h.after(lambda: p.page.click("#theme-toggle-btn")) == []               # a small move
    assert h.after(lambda: p.go("devices"), 900) == []                            # a page
    tile = "[data-click='H.toggleDevice'][data-args*='desk_lamp']"
    p.page.wait_for_selector(tile)
    was_on = p.js(f"document.querySelector(\"{tile}\").getAttribute('aria-pressed')") == "true"
    assert h.after(lambda: p.page.click(tile)) == ["off" if was_on else "on"]     # a switch, by direction

    assert h.after(lambda: p.page.click("#sound-btn")) == []                      # muted
    assert h.after(lambda: p.page.click(tile)) == []
    assert h.after(lambda: p.page.click("#sound-btn")) == ["on"]                  # back on, and says so

    p.js(f"document.querySelector(\"[data-click='H.deleteDevice'][data-args*='desk_lamp']\").click()")
    p.page.wait_for_timeout(300)
    assert h.after(lambda: p.page.click("#m-confirm-cancel")) == []               # a cancel is silent
    p.js(f"document.querySelector(\"[data-click='H.deleteDevice'][data-args*='desk_lamp']\").click()")
    p.page.wait_for_timeout(300)
    assert h.after(lambda: p.page.click("#m-confirm-ok"), 900)[:1] == ["delete"]  # the trash

    # A mode row is built from the state push (no push without the server's
    # workers here), with a plain listener: one like it, as renderModes makes it.
    p.js("""(() => { const r = document.createElement('div'); r.className = 'mode-row on';
        r.dataset.mode = 'test'; r.addEventListener('click', () => r.classList.toggle('on'));
        document.getElementById('modes-pills').appendChild(r); })()""")
    flip_row = lambda: p.js("document.querySelector(\".mode-row[data-mode='test']\").click()")
    assert h.after(flip_row) == ["off"]
    assert h.after(flip_row) == ["on"]
    p.assert_clean()


def test_an_outcome_is_heard_once(admin):
    h = Heard(admin)
    assert h.after(lambda: admin.js("G.showToast('Saved', 'success')"), 300) == ["success"]
    assert h.after(lambda: admin.js("G.showToast('Just so you know', 'info')"), 300) == []
    assert h.after(lambda: admin.js("G.showToast('That did not work', 'error')"), 300) == ["error"]


def test_audio_sleeps_between_sounds(admin):
    # A running context renders silence in real time; in Safari that lagged
    # every transition on the page. It runs for a sound and rests after.
    admin.page.mouse.click(5, 300)                                    # a tap opens audio
    admin.js("G.showToast('Saved', 'success')")
    admin.page.wait_for_timeout(200)
    assert admin.js("Feel.state()") == "running"
    admin.page.wait_for_timeout(2800)                                 # the chime (0.65 s) and the rest
    assert admin.js("Feel.state()") == "suspended"
    # A press on a silent control leaves audio asleep; on one that sounds,
    # it wakes audio before the click lands.
    admin.js("""(() => { for (const [id, cue] of [['quiet-btn', '~light'], ['loud-btn', 'on']]) {
        const b = document.createElement('button'); b.id = id; b.dataset.sound = cue; b.textContent = id;
        b.style.cssText = 'position:fixed;left:20px;top:' + (id === 'quiet-btn' ? 200 : 260) + 'px;z-index:9999';
        document.body.appendChild(b); } })()""")
    for sel, awake in (("#quiet-btn", "suspended"), ("#loud-btn", "running")):
        box = admin.page.locator(sel).bounding_box()
        admin.page.mouse.move(box["x"] + 5, box["y"] + 5)
        admin.page.mouse.down()
        admin.page.wait_for_timeout(120)
        assert admin.js("Feel.state()") == awake, sel
        admin.page.mouse.up()
        admin.page.wait_for_timeout(2600)


# Safari's navigator.audioSession, recorded: Chromium has none. It starts
# at whatever an earlier page in the tab left (Safari keeps it per process).
SESSION = """Object.defineProperty(navigator, 'audioSession', { value: {
  _t: %r, get type() { return this._t; },
  set type(v) { this._t = v; (window.__sessions = window.__sessions || []).push(v); } } });"""


@pytest.mark.parametrize("device, left_behind, rest", [("phone", "auto", "playback"),
                                                        ("mac", "playback", "auto")])
def test_a_narada_call_gets_the_microphone_session(server, browser, device, left_behind, rest):  # noqa: F811
    # Safari refuses the microphone unless the session is auto or
    # play-and-record ("AudioSession category is not compatible with audio
    # capture"). A call switches to play-and-record and nothing may switch
    # it back until the call ends; at rest the page clears a stale value.
    real = browser.new_context

    def make(**kw):
        if device == "phone":
            kw.update(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        ctx = _recording(real(**kw))
        ctx.add_init_script(SESSION % left_behind)
        return ctx
    browser.new_context = make
    try:
        p = Page(browser, server, "admin")
    finally:
        browser.new_context = real
    try:
        touch = p.js("matchMedia('(hover: none) and (pointer: coarse)').matches")
        if touch != (device == "phone"):
            pytest.skip("this engine does not emulate this device")
        assert p.js("navigator.audioSession.type") == rest                 # set at load
        p.js("Feel.voice(true)")
        assert p.js("navigator.audioSession.type") == "play-and-record"
        p.js("Feel.sound('voice-start'); Feel.toast('success')")          # sounds during the call
        p.page.wait_for_timeout(1800)                                      # and the context made at load
        assert p.js("navigator.audioSession.type") == "play-and-record"
        p.js("Feel.voice(false)")
        assert p.js("navigator.audioSession.type") == rest
    finally:
        p.close()


# Every AudioContext the page makes, and every camera frame it decodes.
COUNT = """(() => {
  window.__contexts = 0; window.__decodes = 0;
  const AC = window.AudioContext;
  if (AC) window.AudioContext = class extends AC {
    constructor(...a) { super(...a); window.__contexts++; (window.__ctxStacks = window.__ctxStacks || []).push(new Error().stack); } };
  const dec = HTMLImageElement.prototype.decode;
  HTMLImageElement.prototype.decode = function () { window.__decodes++; return dec.call(this); };
})()"""


def test_camera_sound_shares_the_click_context_and_rests_off_the_dashboard(
        server, browser, monkeypatch):  # noqa: F811
    # The camera's sound once made its own context and kept it, and the
    # frame decoding, running on every page: a second real-time audio
    # thread beside the clicks', and work nobody saw. Now it plays through
    # the clicks' context, only while the camera card is on screen.
    import sys
    import threading

    import cv2
    import numpy as np

    import Garuda_web as gw
    from basic_pipelines.garuda_core import audio_feed as af

    script = ("import sys,time\n"
              "for i in range(3000):\n"
              f"    sys.stdout.buffer.write((300).to_bytes(2,'little',signed=True)*{af.CHUNK_BYTES // 2}); "
              "sys.stdout.flush(); time.sleep(0.02)\n")
    feed = af.AudioFeed(command=lambda: [sys.executable, "-c", script])
    monkeypatch.setattr(gw, "AUDIO_FEED", feed)
    jpeg = cv2.imencode(".jpg", np.full((90, 160, 3), 80, np.uint8))[1].tobytes()
    monkeypatch.setattr(gw.STATE.camera, "frame_buffer", jpeg)
    monkeypatch.setattr(gw.STATE.camera, "frame_ts", time.time())
    stop = threading.Event()

    def frames():                                    # a camera at ~20 fps
        while not stop.wait(0.05):
            gw.STATE.camera.frame_seq += 1
    threading.Thread(target=frames, daemon=True).start()

    real = browser.new_context

    def make(**kw):
        ctx = _recording(real(**kw))
        ctx.add_init_script(COUNT)
        return ctx
    browser.new_context = make
    try:
        p = Page(browser, server, "admin")
    finally:
        browser.new_context = real
    try:
        p.page.wait_for_timeout(1500)                # sound.js made its context
        p.page.click("#cam-toggle-btn")
        p.page.wait_for_timeout(1500)
        assert p.js("document.getElementById('cam-sound-btn').getAttribute('aria-pressed')") == "true"
        assert p.js("window.__contexts") == 1, p.js("window.__ctxStacks")        # one context for clicks and camera
        assert p.js("Feel.state()") == "running"
        assert p.js("window.__decodes") > 3          # the picture is live

        p.go("devices")
        p.page.wait_for_timeout(2400)                # the sound stops; audio rests
        assert p.js("Feel.state()") == "suspended"
        before = p.js("window.__decodes")
        p.page.wait_for_timeout(1000)
        assert p.js("window.__decodes") == before    # no frame decoded off screen

        p.go("dashboard")
        p.page.wait_for_timeout(1200)
        assert p.js("Feel.state()") == "running"     # back with the picture
        assert p.js("window.__decodes") > before
        assert p.js("window.__contexts") == 1
        p.assert_clean()
    finally:
        stop.set()
        p.close()
        feed.stop()

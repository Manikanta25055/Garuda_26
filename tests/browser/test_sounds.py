"""The interface sounds, heard in a real browser: each control plays its sound.

sound.js decides which control plays what; these tests click the real page
and record every sound that starts (by the file it was decoded from) and
every haptic asked for. PW_ENGINE=webkit runs them in Safari's engine, which
is where the owner listens; CI runs Chromium.
"""
import os

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
    admin.page.mouse.move(400, 300)
    admin.page.mouse.down()                                          # a press wakes it before the click
    admin.page.wait_for_timeout(120)
    assert admin.js("Feel.state()") == "running"
    admin.page.mouse.up()

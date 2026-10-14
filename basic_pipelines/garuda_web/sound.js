/* Sound and touch: the interface's sounds and haptics, in one place.

   Modelled on how Apple uses them. Most taps are silent: a sound marks only a
   moment that matters (a switch flipped, a photo taken, a message sent, a
   task done, a lock). Small moves (tabs, filters, pages, sheets) get a haptic
   tick and nothing to hear. Each sound means one thing everywhere, and pitch
   carries direction: rising = on / start / sent, falling = off / stop.

   The haptics follow UIFeedbackGenerator's three kinds: selection (a value
   moved), impact (light / medium / heavy), notification (success / warning
   / error). Android takes them through navigator.vibrate. iOS has no
   vibrate; Safari 18 ticks when a switch control flips, so a hidden one is
   flipped instead (one strength only, and only inside a tap).

   Sounds are quiet, play through Web Audio (no lag), and on iOS use the
   "ambient" audio session: the ring/silent switch mutes them and they never
   stop the person's music. The speaker button in the top bar mutes them on
   this device. The security alarm is not an interface sound and ignores it.

   Which control plays what: CLICK below (by its data-click handler), RULES
   (by selector), or a data-sound="name" / "~haptic" / "none" on the element.
   Outcomes come from toasts (success, warning, error); a confirmed
   "Delete" dialog plays delete. The files are made by
   scripts/make_sounds.py. */
(function () {
  'use strict';

  const BASE = '/static/sounds/';
  const VERSION = 1;
  // Gain per file, measured so each lands at its level: switches about
  // -28 dB, lock / delete / outcomes about -24, the alarm -11 (RMS of the
  // audible part, roughly weighted the way ears are). Clicks sit lowest,
  // outcomes a little above, the alarm far on top.
  const GAIN = {
    on: 0.25, off: 0.21, lock: 0.31, unlock: 0.20, delete: 0.32, shutter: 0.21,
    send: 0.24, receive: 0.18, success: 0.34, warning: 0.25, error: 0.42,
    'rec-start': 0.27, 'rec-stop': 0.21, 'voice-start': 0.24, 'voice-end': 0.18,
    alert: 1.0,
  };
  // The haptic each sound carries.
  const FEEL = {
    on: 'light', off: 'light', lock: 'medium', unlock: 'light', delete: 'medium',
    shutter: 'medium', send: 'light', receive: null, success: 'success',
    warning: 'warning', error: 'error', 'rec-start': 'medium', 'rec-stop': 'medium',
    'voice-start': 'light', 'voice-end': 'light', alert: 'heavy',
  };
  const PATTERN = {
    selection: 4, light: 8, medium: 12, heavy: 20,
    success: [10, 70, 14], warning: [16, 90, 16], error: [20, 50, 20, 50, 20],
  };
  const NAMES = Object.keys(GAIN);

  // A switch's new state: aria-pressed / aria-checked, else an on / active
  // class, read before its handler flips it.
  const flip = el => {
    const aria = el.getAttribute('aria-pressed') || el.getAttribute('aria-checked');
    const was = aria != null ? aria === 'true' : el.classList.contains('on') || el.classList.contains('active');
    return was ? 'off' : 'on';
  };

  // data-click handler -> what it plays. "~kind" = haptic only.
  const CLICK = {
    // a value moved
    'G.switchDocsTab': '~selection', 'G.switchLogTab': '~selection', 'G.switchCamTab': '~selection',
    'G.fbFilterInbox': '~selection', 'G.fbSwitchTab': '~selection', 'G.toggleActivityKind': '~selection',
    'G.stepActivityDay': '~selection', 'H.toggleDay': '~selection', 'H.pickStep': '~selection',
    'G.toggleTheme': '~selection',
    // moving around
    'G.nav': '~light', 'N.toggleSheet': '~light', 'N.toggleInfo': '~light', 'G.toggleFeedback': '~light',
    'G.closeFeedback': '~light', 'G.openDocs': '~light', 'G.openAddCmd': '~light', 'G.closeModal': '~light',
    'G.openBackendConfig': '~light', 'H.openSceneEditor': '~light', 'H.dismissNotice': '~light',
    'H.dismissSuggestion': '~light', 'H.discardProposal': '~light',
    // switches
    'H.toggleDevice': flip, 'G.toggleCamera': flip, 'G.togglePrivacy': flip, 'G.toggleNightPresence': flip,
    'H.toggleSetting': flip, 'H.toggleRule': flip, 'H.toggleSchedule': flip, 'H.toggleShortcut': flip,
    'H.setEnabled': flip,
    'H.allOff': 'off',
    // moments
    'G.takeSnapshot': 'shutter', 'H.instruct': 'send', 'G.submitFeedback': 'send',
    'G.sendAdminOTP': 'send', 'G.sendForgotOTP': 'send', 'G.requestMkOtp': 'send', 'G.testEmail': 'send',
    // these sound when they finish (narada.js, app.js), not when pressed
    'N.submit': 'none', 'N.toggleVoice': 'none', 'G.toggleClip': 'none', 'G.logout': 'none',
    'Feel.toggle': 'none',
  };
  // Controls built without a data-click handler.
  const RULES = [
    ['.mode-row', flip],
    ['.ios-item, .more-tile', '~light'],
  ];

  let ctx = null, buffers = {}, loading = null, lastAt = {}, lastSound = 0, lastTouch = 0;
  let muted = false;
  try { muted = localStorage.getItem('garuda_sound') === 'off'; } catch (_) {}

  // ── Audio ──────────────────────────────────────────────────
  function session(type) {
    try { if (navigator.audioSession) navigator.audioSession.type = type; } catch (_) {}
  }

  function wake() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      session('ambient');
      try { ctx = new AC({ latencyHint: 'interactive' }); } catch (_) { return null; }
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    if (!loading) loading = load();
    return ctx;
  }

  function load() {
    return Promise.all(NAMES.map(name =>
      fetch(`${BASE}${name}.mp3?v=${VERSION}`)
        .then(r => r.ok ? r.arrayBuffer() : Promise.reject(r.status))
        .then(data => new Promise((ok, no) => ctx.decodeAudioData(data, ok, no)))
        .then(buf => { buffers[name] = { buf, at: leadIn(buf) }; })
        .catch(() => {})));
  }

  // MP3 starts with a few milliseconds of encoder padding, which some
  // browsers keep. A click that lands late reads as lag, so start where the
  // sound does.
  function leadIn(buf) {
    const d = buf.getChannelData(0);
    const n = Math.min(d.length, Math.floor(buf.sampleRate * 0.08));
    for (let i = 0; i < n; i++) if (Math.abs(d[i]) > 0.01) return Math.max(0, i - 24) / buf.sampleRate;
    return 0;
  }

  function sound(name, force) {
    if ((muted && !force) || document.hidden) return false;
    const b = buffers[name];
    if (!wake() || !b) return false;
    const now = performance.now();
    if (now - (lastAt[name] || 0) < 80) return false;
    lastAt[name] = now;
    lastSound = now;
    const src = ctx.createBufferSource();
    const gain = ctx.createGain();
    gain.gain.value = GAIN[name] || 0.3;
    src.buffer = b.buf;
    src.connect(gain).connect(ctx.destination);
    src.start(0, b.at);
    return true;
  }

  // ── Touch ──────────────────────────────────────────────────
  let iosSwitch = null;
  function touch(kind) {
    if (!kind) return;
    const now = performance.now();
    if (now - lastTouch < 50) return;
    lastTouch = now;
    if (navigator.vibrate) { try { navigator.vibrate(PATTERN[kind] || 8); } catch (_) {} return; }
    if (!iosSwitch) {
      iosSwitch = document.createElement('label');
      iosSwitch.className = 'sx-haptic';
      iosSwitch.setAttribute('aria-hidden', 'true');
      iosSwitch.innerHTML = '<input type="checkbox" switch tabindex="-1">';
      document.body.appendChild(iosSwitch);
    }
    iosSwitch.click();
  }

  // ── Cues ───────────────────────────────────────────────────
  function play(name) {
    sound(name);
    touch(FEEL[name]);
  }

  function cue(c) {
    if (!c || c === 'none') return;
    if (c[0] === '~') touch(c.slice(1));
    else play(c);
  }

  function cueFor(el) {
    const own = el.closest('[data-sound]');
    if (own) return own.getAttribute('data-sound');
    const btn = el.closest('[data-click]');
    if (btn) {
      const c = CLICK[btn.getAttribute('data-click')];
      return typeof c === 'function' ? c(btn) : c;
    }
    for (const [sel, c] of RULES) {
      const hit = el.closest(sel);
      if (hit) return typeof c === 'function' ? c(hit) : c;
    }
    return null;
  }

  // Capture phase: read a switch before its own handler flips it.
  document.addEventListener('click', ev => {
    const el = ev.target;
    if (!(el instanceof Element) || el.closest('.sx-haptic, .nx-haptic')) return;
    if (el.closest('button:disabled, [aria-disabled="true"]')) return;
    cue(cueFor(el));
  }, true);
  // The first touch anywhere opens audio, so the first real cue is not lost.
  ['pointerdown', 'keydown'].forEach(t => document.addEventListener(t, wake, { once: true, capture: true }));

  // ── Public ─────────────────────────────────────────────────
  // A toast's outcome. A success right after a cue for the same act is the
  // same moment and stays quiet; warnings and errors always speak.
  function toast(type) {
    if (type === 'success' && performance.now() - lastSound < 1500) return;
    if (type === 'success' || type === 'warning' || type === 'error') play(type);
  }

  // Narada's voice needs the normal audio session (microphone, full volume).
  function voice(active) { session(active ? 'auto' : 'ambient'); }

  // The alarm plays whatever the switch says. False when the sound is not
  // loaded yet, so the caller can fall back to its own tone.
  function alarm() { return sound('alert', true); }

  function paintButton() {
    const b = document.getElementById('sound-btn');
    if (!b) return;
    b.classList.toggle('is-muted', muted);
    b.setAttribute('aria-pressed', String(!muted));
    b.title = muted ? 'Sounds off' : 'Sounds on';
    b.setAttribute('aria-label', b.title);
  }

  function toggle() {
    muted = !muted;
    try { localStorage.setItem('garuda_sound', muted ? 'off' : 'on'); } catch (_) {}
    paintButton();
    if (muted) touch('light'); else play('on');
  }

  document.addEventListener('DOMContentLoaded', paintButton);
  if (document.readyState !== 'loading') paintButton();

  window.Feel = { play, sound: name => sound(name), touch, toast, voice, alarm, toggle, get muted() { return muted; } };
})();

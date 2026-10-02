/* Narada: one page to talk or type to the house.

   Two things on the page. The dot field, edge to edge. And the dock: the
   black bar you type in, which opens upward into the whole conversation
   (typed and spoken turns alike) and minimises back to the bar. Nothing is
   written over the field itself.

   The glyph is a full-page dot field: concentric rings packed close, the
   dots shrinking from a large core outward to the edges of the screen.
   Every dot's size and brightness come from one "energy" value, and each
   state shapes that energy differently:

     idle        a slow breath rolling outward across the whole field
     hover       dots near the pointer swell and lean toward it
     connecting  rings light up one after another
     listening   each direction follows a band of the microphone's spectrum
     thinking    a comet orbits the core, trailing light
     talking     rings bloom with Narada's own voice, low notes inside

   All dots are drawn in a few batched fills per frame (grouped by colour
   and brightness), which keeps ~4,000 dots smooth on a phone.

   Voice runs through ElevenLabs (microphone, speech-to-text, speaking the
   reply). What Narada says and does is decided on the Pi by NVIDIA NIM:
   the browser only ever gets a one-conversation token. */
const N = (() => {
  'use strict';

  const RAYS = 48, RINGS = 14;          // spectrum bins by direction / by distance
  const ALPHA_STEPS = 14;
  const $ = id => document.getElementById(id);
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  let canvas, ctx, CW = 0, CH = 0, DPR = 1;   // canvas px; H is home.js
  let state = 'idle';                 // see header
  let stateSince = performance.now();
  let raf = 0, slowTimer = 0, pending = false, visible = false;
  let pointer = null;                 // {x, y} in canvas px while hovering
  const level = new Float32Array(RAYS);   // smoothed per-ray energy (0..1)
  const ringLevel = new Float32Array(RINGS);
  let ink = '#111', accent = '#111', danger = '#ff453a';
  // The dot field, rebuilt on resize: position, direction, distance, size.
  let D = null, cx = 0, cy = 0, Rfx = 1, coreR = 1;
  let synthTalkUntil = 0;             // text replies animate "talking" too

  // ── Voice session ──────────────────────────────────────────
  let conv = null, voice = 'off';     // off | connecting | on
  let clientMod = null, busy = false, lastUserAt = 0;
  let lastReply = null, infoOpen = false, infoData = null;
  // The conversation: [{who: 'you' | 'narada' | 'error', text}]; kept for the
  // tab's lifetime so a reload or a trip to another page does not lose it.
  const SESSION_KEY = 'narada-session', SESSION_MAX = 80;
  let session = [], sheetOpen = false, userMinimised = false, pendingEl = null;

  function voiceActive() { return voice !== 'off'; }

  function setState(s) {
    if (s === state) return;
    state = s;
    stateSince = performance.now();
    _syncChrome();
  }

  // ── Haptics ────────────────────────────────────────────────
  // Android: the Vibration API. iOS Safari has none; since iOS 18 tapping an
  // <input switch> gives a system tick, so a hidden one is clicked instead.
  let lastHaptic = 0;
  const PATTERNS = { tap: 8, start: [10, 50, 16], stop: 14, talk: 5, error: [28, 60, 28], send: 9 };
  function haptic(kind) {
    const now = performance.now();
    if (now - lastHaptic < 70) return;
    lastHaptic = now;
    if (navigator.vibrate) { try { navigator.vibrate(PATTERNS[kind] || 8); } catch (_) {} return; }
    const sw = $('nx-haptic');
    if (sw && sw.parentElement) sw.parentElement.click();
  }

  // ── Canvas ─────────────────────────────────────────────────
  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    ink = cs.getPropertyValue('--t1').trim() || ink;
    accent = cs.getPropertyValue('--accent').trim() || accent;
    danger = cs.getPropertyValue('--danger').trim() || danger;
  }

  function resize() {
    if (!canvas) return;
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    // Four times the pixels at DPR 2: too much for a mid-range phone's canvas.
    const lite = document.documentElement.classList.contains('perf-lite');
    DPR = Math.min(window.devicePixelRatio || 1, lite ? 1.25 : 2);
    CW = Math.round(r.width * DPR);
    CH = Math.round(r.height * DPR);
    canvas.width = CW; canvas.height = CH;
    buildField(r);
  }

  // Concentric rings from the core to the farthest corner. Dot size falls
  // off with distance; ring spacing and dots per ring follow the size, so
  // neighbours stay a small, even gap apart at every radius.
  function buildField(rect) {
    // Centred on the page above the dock, so the core stays a clear target.
    const dock = $('nx-bar');
    const floor = dock ? dock.getBoundingClientRect().top - rect.top : rect.height - 120;
    cx = CW / 2;
    cy = Math.min(rect.height * 0.46, (64 + floor) / 2) * DPR;
    Rfx = Math.max(60 * DPR, Math.min(CW / 2, cy - 40 * DPR) * 0.98);
    coreR = Rfx * 0.075;
    const Rmax = Math.hypot(Math.max(cx, CW - cx), Math.max(cy, CH - cy)) + 4 * DPR;
    const s0 = Rfx * 0.03;
    const sizeAt = r => s0 * (0.14 + 0.86 * Math.exp(-1.55 * r / Rfx));
    const pts = [];
    let r = coreR + s0 * 1.7, ring = 0;
    while (r < Rmax) {
      const sz = sizeAt(r), gap = Math.max(sz * 0.5, 1.2 * DPR);
      const n = Math.max(6, Math.floor((2 * Math.PI * r) / (2 * sz + gap)));
      const off = (ring % 2) * Math.PI / n;
      for (let i = 0; i < n; i++) {
        const ang = off + (i / n) * Math.PI * 2 - Math.PI / 2;
        const x = cx + Math.cos(ang) * r, y = cy + Math.sin(ang) * r;
        if (x < -sz || y < -sz || x > CW + sz || y > CH + sz) continue;
        pts.push(x, y, ang, r / Rfx, sz);
      }
      const next = sizeAt(r + 2 * sz + gap);
      r += sz + next + gap;
      ring++;
    }
    const n = pts.length / 5;
    D = { n, x: new Float32Array(n), y: new Float32Array(n), ang: new Float32Array(n),
          rf: new Float32Array(n), size: new Float32Array(n), bin: new Uint8Array(n),
          rbin: new Uint8Array(n) };
    for (let i = 0; i < n; i++) {
      const ang = pts[i * 5 + 2], rf = pts[i * 5 + 3];
      D.x[i] = pts[i * 5]; D.y[i] = pts[i * 5 + 1]; D.ang[i] = ang; D.rf[i] = rf; D.size[i] = pts[i * 5 + 4];
      const u = ((ang + Math.PI / 2) / (Math.PI * 2) % 1 + 1) % 1;
      D.bin[i] = Math.min(RAYS - 1, Math.floor(u * RAYS));
      D.rbin[i] = Math.min(RINGS - 1, Math.floor(Math.min(rf, 0.999) * RINGS));
    }
  }

  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const angDist = (a, b) => { const d = Math.abs(a - b) % (Math.PI * 2); return d > Math.PI ? Math.PI * 2 - d : d; };

  // Spectrum -> per-direction targets, mirrored left/right so the field
  // stays balanced; low frequencies point up.
  function spectrumTargets(bins, gain) {
    const out = new Float32Array(RAYS);
    if (!bins || !bins.length) return out;
    const usable = Math.floor(bins.length * 0.6);
    for (let i = 0; i < RAYS; i++) {
      const half = i <= RAYS / 2 ? i / (RAYS / 2) : (RAYS - i) / (RAYS / 2);
      const idx = Math.min(usable - 1, Math.floor(Math.pow(half, 1.5) * usable));
      out[i] = clamp((bins[idx] / 255) * gain);
    }
    return out;
  }

  function updateLevels(t) {
    let targets = null, rings = null;
    if (conv && state === 'listening') {
      try { targets = spectrumTargets(conv.getInputByteFrequencyData(), 1.5); } catch (_) {}
    } else if (conv && state === 'talking') {
      try {
        const bins = conv.getOutputByteFrequencyData();
        targets = spectrumTargets(bins, 1.3);
        rings = new Float32Array(RINGS);
        const usable = Math.floor(bins.length * 0.5);
        for (let r = 0; r < RINGS; r++) rings[r] = clamp(bins[Math.floor((r / RINGS) * usable)] / 255 * 1.4);
      } catch (_) {}
    } else if (state === 'talking') {
      // Text reply: a soft synthetic voice so the field still "speaks".
      targets = new Float32Array(RAYS);
      rings = new Float32Array(RINGS);
      for (let i = 0; i < RAYS; i++) targets[i] = 0.35 + 0.3 * Math.sin(t * 7.1 + i * 0.9) * Math.sin(t * 3.3 + i * 0.37);
      for (let r = 0; r < RINGS; r++) rings[r] = 0.45 + 0.35 * Math.sin(t * 9 - r * 0.8);
    }
    for (let i = 0; i < RAYS; i++) {
      const tgt = targets ? targets[i] : 0;
      level[i] += (tgt - level[i]) * (tgt > level[i] ? 0.45 : 0.12);
    }
    for (let r = 0; r < RINGS; r++) {
      const tgt = rings ? rings[r] : 0;
      ringLevel[r] += (tgt - ringLevel[r]) * (tgt > ringLevel[r] ? 0.5 : 0.15);
    }
  }

  // Energy of one dot (0..1). rf is distance / active radius: the effects
  // play out inside rf <= 1 and ripple more faintly beyond it.
  function energy(i, t, since) {
    const rf = D.rf[i], a = D.bin[i];
    const breath = 0.16 + 0.16 * (0.5 + 0.5 * Math.sin(t * 1.25 - rf * 5.5));
    let e;
    switch (state) {
      case 'listening': {
        const reach = 0.1 + level[a] * 1.1;
        e = rf <= reach ? Math.max(breath, 1 - (rf / Math.max(reach, 0.01)) * 0.5) : breath;
        break;
      }
      case 'thinking': {
        const d = angDist(D.ang[i], t * 3.1 - Math.PI / 2) + Math.abs(rf - 0.55) * 2.2;
        e = Math.max(breath * 0.8, Math.exp(-d * 2.2) * (0.7 + 0.3 * Math.sin(t * 6 - rf * 9)));
        break;
      }
      case 'talking':
        e = clamp(breath + ringLevel[D.rbin[i]] * 0.75 * (1 - rf * 0.3) + level[a] * 0.3 * (1 - rf));
        break;
      case 'connecting': {
        const lit = ((t - since) * 1.6) % 1.6;
        e = Math.max(breath, Math.exp(-Math.abs(rf - lit) * 9) * 0.9);
        break;
      }
      case 'error':
        e = breath + 0.6 * Math.exp(-(t - since) * 2.5) * Math.max(0, 1 - rf);
        break;
      default:
        e = breath;
    }
    return rf > 1 ? e * Math.exp(-(rf - 1) * 1.1) : e;
  }

  // Per-frame buckets: [colour][alpha step] -> flat [x, y, r, ...].
  const buckets = [[], []];
  for (let c = 0; c < 2; c++) for (let k = 0; k < ALPHA_STEPS; k++) buckets[c].push([]);

  function schedule() {
    if (!visible || pending) return;
    pending = true;
    if (reduceMotion.matches || state === 'idle') {
      slowTimer = setTimeout(() => { raf = requestAnimationFrame(draw); }, 33);
    } else {
      raf = requestAnimationFrame(draw);
    }
  }

  function draw(now) {
    pending = false; raf = 0;
    if (!visible || !ctx || !D) { schedule(); return; }
    const t = now / 1000, since = stateSince / 1000;
    updateLevels(t);
    if (state === 'talking' && !conv && now > synthTalkUntil) setState('idle');

    for (let c = 0; c < 2; c++) for (let k = 0; k < ALPHA_STEPS; k++) buckets[c][k].length = 0;
    const hoverOn = pointer && (state === 'idle' || state === 'listening');
    const hr = Rfx * 0.32, hr2 = hr * hr;
    const active = state !== 'idle';
    for (let i = 0; i < D.n; i++) {
      let e = energy(i, t, since);
      let x = D.x[i], y = D.y[i];
      if (hoverOn) {
        const dx = pointer.x - x, dy = pointer.y - y, d2 = dx * dx + dy * dy;
        if (d2 < hr2 * 4) {
          const pull = Math.exp(-d2 / hr2);
          e = clamp(e + pull * 0.6);
          x += dx * pull * 0.08; y += dy * pull * 0.08;
        }
      }
      const size = D.size[i] * (0.42 + 0.58 * clamp(e * 1.25));
      const alpha = clamp(0.1 + 0.9 * e);
      const k = Math.min(ALPHA_STEPS - 1, Math.round(alpha * (ALPHA_STEPS - 1)));
      if (k === 0) continue;
      const c = active && e > 0.62 && state === 'error' ? 1 : 0;
      buckets[c][k].push(x, y, Math.max(size, 0.55 * DPR));
    }

    ctx.clearRect(0, 0, CW, CH);
    const colours = [ink, danger];
    for (let c = 0; c < 2; c++) {
      ctx.fillStyle = colours[c];
      for (let k = 1; k < ALPHA_STEPS; k++) {
        const b = buckets[c][k];
        if (!b.length) continue;
        ctx.globalAlpha = k / (ALPHA_STEPS - 1);
        ctx.beginPath();
        for (let j = 0; j < b.length; j += 3) {
          ctx.moveTo(b[j] + b[j + 2], b[j + 1]);
          ctx.arc(b[j], b[j + 1], b[j + 2], 0, Math.PI * 2);
        }
        ctx.fill();
      }
    }

    // Core: breathes when idle, follows the loudest band otherwise.
    let peak = 0;
    for (let i = 0; i < RAYS; i++) peak = Math.max(peak, level[i]);
    const pulse = state === 'thinking' ? 0.5 + 0.5 * Math.sin(t * 6)
      : state === 'idle' ? 0.5 + 0.5 * Math.sin(t * 1.25) : peak;
    ctx.globalAlpha = 1;
    ctx.fillStyle = state === 'error' ? danger : ink;
    ctx.beginPath();
    ctx.arc(cx, cy, coreR * (1 + 0.28 * pulse), 0, Math.PI * 2);
    ctx.fill();

    _driveIsland(peak);
    schedule();
  }

  function start() { visible = true; schedule(); }
  function stop() {
    visible = false;
    clearTimeout(slowTimer);
    if (raf) cancelAnimationFrame(raf);
    raf = 0; pending = false;
  }

  // ── Island + chrome ────────────────────────────────────────
  function _driveIsland(peak) {
    const hud = $('top-hud');
    if (!hud || !hud.classList.contains('di-voice')) return;
    const bars = hud.querySelectorAll('.di-vbar');
    const src = state === 'talking' || state === 'listening';
    hud.classList.toggle('di-live-levels', !!conv && src);
    if (!conv || !src) return;
    const step = Math.floor(RAYS / 2 / bars.length);
    bars.forEach((b, i) => b.style.setProperty('--v', (0.25 + 0.75 * level[i * step]).toFixed(3)));
  }

  function _syncChrome() {
    const hud = $('top-hud'), lbl = $('di-voice-lbl'), mic = $('nx-mic'), status = $('nx-status');
    if (hud) hud.classList.toggle('di-talk', state === 'talking');
    if (lbl) lbl.textContent = state === 'talking' ? 'Speaking' : state === 'thinking' ? 'Thinking' : 'Listening';
    if (mic) {
      mic.classList.toggle('on', voice !== 'off');
      mic.classList.toggle('connecting', voice === 'connecting');
      mic.setAttribute('aria-pressed', String(voice !== 'off'));
      mic.setAttribute('aria-label', voice === 'off' ? 'Talk to Narada' : 'End conversation');
    }
    const dock = $('nx-dock');
    if (dock) dock.dataset.state = state;
    if (status) {
      status.textContent = {
        idle: voice === 'on' ? 'Listening' : 'Tap the core to talk · or type',
        connecting: 'Connecting…',
        listening: 'Listening',
        thinking: 'Thinking',
        talking: 'Speaking',
        error: status.dataset.err || 'Something went wrong',
      }[state] || '';
    }
    if (window.G && G.setDI) {
      // Typed questions show the island's thinking pill; voice has its own.
      const hud = $('top-hud');
      if (state === 'thinking' && voice === 'off') G.setDI('thinking');
      else if (hud && hud.classList.contains('di-thinking')) G.setDI('');
      else G.syncDI();
    }
  }

  // ── Conversation ───────────────────────────────────────────
  function save() {
    try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(session.slice(-SESSION_MAX))); } catch (_) {}
  }
  function restore() {
    try { session = JSON.parse(sessionStorage.getItem(SESSION_KEY) || '[]') || []; } catch (_) { session = []; }
    if (!Array.isArray(session)) session = [];
    for (const m of session) bubble(m.who, m.text);
    syncDock();
  }

  function scrollLog() {
    const log = $('nx-log');
    if (log) log.scrollTop = log.scrollHeight;
  }
  function bubble(who, text) {
    const log = $('nx-log');
    if (!log) return null;
    const el = document.createElement('div');
    el.className = 'nx-msg ' + (who === 'error' ? 'narada error' : who);
    el.textContent = text || '';
    log.appendChild(el);
    return el;
  }

  // "Narada is working on it": three dots where the reply will appear.
  function setPending(on) {
    if (pendingEl) { pendingEl.remove(); pendingEl = null; }
    const log = $('nx-log');
    if (!on || !log) return;
    pendingEl = document.createElement('div');
    pendingEl.className = 'nx-msg narada pending';
    pendingEl.innerHTML = '<i></i><i></i><i></i>';
    log.appendChild(pendingEl);
    scrollLog();
  }

  // The "i" button on phones sits just above the dock; tell CSS how tall
  // the minimised dock is (the open sheet hides the button instead).
  function measureDock() {
    // Bar plus the peek line: the dock's own height is no use here, it is
    // still the tall open sheet while the closing animation runs.
    const bar = $('nx-bar'), peek = $('nx-peek'), page = $('page-narada');
    if (!bar || !page || !bar.offsetHeight) return;
    const peekH = !sheetOpen && peek && peek.offsetParent ? peek.offsetHeight : 0;
    page.style.setProperty('--nx-dock-h', (bar.offsetHeight + peekH) + 'px');
  }

  function syncDock() {
    const dock = $('nx-dock'), peek = $('nx-peek'), exp = $('nx-expand');
    if (!dock) return;
    const last = session.length ? session[session.length - 1] : null;
    dock.classList.toggle('has-log', session.length > 0);
    dock.classList.toggle('open', sheetOpen);
    dock.classList.toggle('has-peek', !!last && last.who !== 'you');
    if (peek) peek.textContent = last ? last.text : '';
    measureDock();
    if (exp) {
      exp.setAttribute('aria-expanded', String(sheetOpen));
      exp.setAttribute('aria-label', sheetOpen ? 'Minimise conversation' : 'Open conversation');
    }
  }

  // Add a turn. The sheet opens by itself unless it was minimised by hand;
  // then the newest line shows above the bar instead.
  function say(who, text, typed) {
    text = (text || '').trim();
    if (!text) return null;
    setPending(false);
    session.push({ who, text });
    if (session.length > SESSION_MAX) session = session.slice(-SESSION_MAX);
    save();
    const el = bubble(who, typed ? '' : text);
    if (!userMinimised) sheetOpen = true;
    syncDock();
    if (typed && el) typeInto(el, text); else scrollLog();
    return el;
  }

  function typeInto(el, text) {
    let i = 0;
    synthTalkUntil = performance.now() + Math.min(6000, 400 + text.length * 22);
    setState('talking');
    (function tick() {
      i = Math.min(i + 3, text.length);
      el.textContent = text.slice(0, i);
      scrollLog();
      if (i < text.length) requestAnimationFrame(tick);
    })();
  }

  function toggleSheet(force) {
    sheetOpen = typeof force === 'boolean' ? force : !sheetOpen;
    userMinimised = !sheetOpen;
    haptic('tap');
    syncDock();
    if (sheetOpen) requestAnimationFrame(scrollLog);
  }

  function clearSession() {
    session = []; save();
    setPending(false);
    const log = $('nx-log');
    if (log) log.innerHTML = '';
    sheetOpen = false; userMinimised = false;
    haptic('tap');
    syncDock();
  }

  // ── Voice ──────────────────────────────────────────────────
  async function loadClient() {
    if (!clientMod) clientMod = await import('/static/vendor/elevenlabs-client.js');
    return clientMod;
  }

  // Short on purpose: every spoken character counts against the plan.
  const GREETINGS = ["Hey, I'm listening.", "Hi! What can I do?", "I'm here. Go ahead.", "Yes?"];

  function voiceError(msg) {
    const status = $('nx-status');
    if (status) status.dataset.err = msg;
    voice = 'off'; conv = null;
    setPending(false);
    setState('error');
    haptic('error');
    if (window.G) G.showToast(msg, 'error');
    setTimeout(() => { if (state === 'error') setState('idle'); }, 2600);
  }

  async function startVoice() {
    if (voice !== 'off') return;
    voice = 'connecting';
    setState('connecting');
    haptic('start');
    try {
      const [mod, tok] = await Promise.all([loadClient(), G._apiFn('POST', '/api/narada/voice/token')]);
      if (voice !== 'connecting') return;           // cancelled meanwhile
      conv = await mod.Conversation.startSession({
        conversationToken: tok.token,
        connectionType: 'webrtc',
        overrides: { agent: { firstMessage: GREETINGS[Math.floor(Math.random() * GREETINGS.length)] } },
        onConnect: () => { voice = 'on'; setState('listening'); _syncChrome(); },
        onDisconnect: () => {
          const wasOn = voice !== 'off';
          conv = null; voice = 'off';
          setPending(false);
          setState('idle');
          if (wasOn) haptic('stop');
        },
        onModeChange: ({ mode }) => {
          if (mode === 'speaking') { setPending(false); setState('talking'); haptic('talk'); }
          else if (voice === 'on') setState('listening');
        },
        onMessage: (m) => {
          const who = m.role || m.source;
          if (who === 'user') {
            say('you', m.message);
            lastUserAt = performance.now();
            setState('thinking');
            setPending(true);
          } else if (m.message) {
            say('narada', m.message);
            pollMemory();                 // a spoken reply carries no chips of its own
          }
        },
        onError: (message) => voiceError(typeof message === 'string' ? message : 'Voice connection failed'),
      });
    } catch (e) {
      const detail = (e && (e.detail || e.message)) || '';
      if (/Permission|NotAllowed|denied/i.test(detail)) voiceError('Microphone access was blocked. Allow it in the browser to talk to Narada.');
      else voiceError(detail || 'Could not start the voice session.');
    }
  }

  async function stopVoice() {
    const c = conv;
    voice = 'off'; conv = null;
    setPending(false);
    if (c) { try { await c.endSession(); } catch (_) {} }
    setState('idle');
    haptic('stop');
  }

  function toggleVoice() {
    if (voice === 'off') startVoice(); else stopVoice();
  }

  // ── Typing ─────────────────────────────────────────────────
  async function submit(ev) {
    if (ev) ev.preventDefault();
    const input = $('nx-input');
    const text = (input && input.value || '').trim();
    if (!text || busy) return;
    input.value = '';
    haptic('send');
    say('you', text);
    setPending(true);
    if (conv && voice === 'on') {           // mid-conversation: Narada answers aloud
      conv.sendUserMessage(text);
      setState('thinking');
      return;
    }
    busy = true;
    setState('thinking');
    try {
      const t0 = performance.now();
      const res = await G._apiFn('POST', '/api/chat', { message: text });
      lastReply = { ...res, seconds: (performance.now() - t0) / 1000 };
      say('narada', res.response || '…', true);
      // A drafted automation needs a Confirm button, so it goes into the
      // conversation; what ran and which model answered go to the "i" panel.
      if (res.proposal && window.H && H.proposalHtml) {
        const card = bubble('extra', '');
        if (card) { card.innerHTML = H.proposalHtml(res.proposal); scrollLog(); }
      }
      memoryChips(res.memory);
      if (infoOpen) renderInfo();
    } catch (e) {
      const msg = (e && e.detail) || 'Connection error. Please try again.';
      say('error', msg);
      voiceError(msg);
    } finally {
      busy = false;
    }
  }

  // ── Memory: what Narada knows about the household ──────────
  // Every change memory makes shows as a chip in the conversation ("Saved to
  // memory", with Undo). The whole memory is one tap away in the "i" panel.
  const escHtml = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const CHIP_LABEL = { saved: 'Saved to memory', updated: 'Memory updated', forgotten: 'Removed from memory', pending: 'Keep this in memory?' };
  const ORIGIN_LABEL = { asked: 'you asked', noticed: 'picked up in conversation', distilled: 'from a past conversation', manual: 'added by hand' };
  const shownEvents = new Set();
  let memSince = Date.now() / 1000;
  let memOpen = false, memData = null, memEditing = null, memError = '';

  function memoryChips(events) {
    let added = false;
    for (const e of events || []) {
      const key = e.id + ':' + e.status;
      if (shownEvents.has(key)) continue;
      shownEvents.add(key);
      if (e.at) memSince = Math.max(memSince, e.at);
      const card = bubble('extra', '');
      if (!card) continue;
      card.classList.add('nx-memchip');
      card.dataset.id = e.id;
      card.dataset.status = e.status;
      if (e.replaced_id) card.dataset.replaced = e.replaced_id;
      const buttons = e.status === 'pending'
        ? '<button type="button" data-chip="confirm">Keep</button><button type="button" data-chip="dismiss">No</button>'
        : '<button type="button" data-chip="undo">Undo</button>';
      card.innerHTML = `<span class="nx-memchip-l">${CHIP_LABEL[e.status] || 'Memory'}</span>`
        + `<span class="nx-memchip-t">${escHtml(e.text)}</span><span class="nx-memchip-b">${buttons}</span>`;
      added = true;
    }
    if (added) { scrollLog(); haptic('tap'); if (memOpen) loadMemory(); }
  }

  async function pollMemory() {
    try {
      const res = await G._apiFn('GET', '/api/narada/memory?since=' + encodeURIComponent(memSince));
      memoryChips(res.recent);
    } catch (_) {}
  }

  // Undo means "as if it had not happened": a new fact goes for good, a
  // corrected one comes back, a removed one is restored.
  async function chipAction(card, what) {
    const id = card.dataset.id, status = card.dataset.status, mem = '/api/narada/memory/';
    const done = (label, note) => {
      const b = card.querySelector('.nx-memchip-b'), l = card.querySelector('.nx-memchip-l');
      if (l && label) l.textContent = label;
      if (b) b.textContent = note || '';
      card.classList.toggle('undone', !!label && label !== 'Saved to memory' && label !== 'Back in memory');
    };
    card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
      if (what === 'confirm') { await G._apiFn('POST', mem + id + '/confirm'); done('Saved to memory'); }
      else if (what === 'dismiss') { await G._apiFn('DELETE', mem + id + '?forever=1'); done('Not kept'); }
      else if (status === 'forgotten') { await G._apiFn('POST', mem + id + '/restore'); done('Back in memory'); }
      else {
        await G._apiFn('DELETE', mem + id + '?forever=1');
        if (card.dataset.replaced) await G._apiFn('POST', mem + card.dataset.replaced + '/restore');
        done(card.dataset.replaced ? 'Change undone' : 'Not saved');
      }
      haptic('tap');
      if (memOpen) loadMemory();
    } catch (e) {
      done('', (e && e.detail) || 'That did not work');
    }
  }

  async function loadMemory() {
    // An error from the action that led here stays on screen: only a failed load replaces it.
    try { memData = await G._apiFn('GET', '/api/narada/memory'); }
    catch (e) { memError = (e && e.detail) || 'Could not load the memory.'; }
    if (infoOpen) renderInfo();
  }

  function memoryHtml() {
    const d = memData;
    if (!d) return `<div class="nx-mem-empty">${escHtml(memError || 'Loading…')}</div>`;
    const row = f => memEditing === f.id
      ? `<form class="nx-mem-row editing" data-form="edit" data-id="${f.id}">
           <input class="nx-mem-in" name="text" maxlength="200" value="${escHtml(f.text)}" aria-label="Fact"/>
           <span class="nx-mem-acts"><button type="submit">Save</button><button type="button" data-mem="cancel">Cancel</button></span>
         </form>`
      : `<div class="nx-mem-row" data-id="${f.id}">
           <span class="nx-mem-text">${escHtml(f.text)}<small>${escHtml(ORIGIN_LABEL[f.origin] || f.origin || '')}${f.by ? ' · ' + escHtml(f.by) : ''}</small></span>
           <span class="nx-mem-acts"><button type="button" data-mem="edit">Edit</button><button type="button" data-mem="remove">Remove</button></span>
         </div>`;
    const groups = (d.categories || []).map(c => {
      const facts = d.facts.filter(f => f.category === c);
      return facts.length ? `<div class="nx-info-h">${escHtml(c)}</div>${facts.map(row).join('')}` : '';
    }).join('');
    const pending = (d.pending || []).length ? `<div class="nx-info-h">Waiting for you</div>` + d.pending.map(f =>
      `<div class="nx-mem-row" data-id="${f.id}"><span class="nx-mem-text">${escHtml(f.text)}<small>Narada was not sure you said this</small></span>
       <span class="nx-mem-acts"><button type="button" data-mem="confirm">Keep</button><button type="button" data-mem="purge">No</button></span></div>`).join('') : '';
    const archived = (d.archived || []).length ? `<details class="nx-mem-old"><summary>Removed (${d.archived.length})</summary>` + d.archived.map(f =>
      `<div class="nx-mem-row" data-id="${f.id}"><span class="nx-mem-text">${escHtml(f.text)}<small>${escHtml({ corrected: 'replaced by a newer fact', forgotten: 'removed' }[f.archived && f.archived.reason] || 'removed')}</small></span>
       <span class="nx-mem-acts"><button type="button" data-mem="restore">Restore</button><button type="button" data-mem="purge">Delete</button></span></div>`).join('') + '</details>' : '';
    return `
      <div class="nx-mem-head"><button type="button" class="nx-mem-back" data-mem="back">Back</button><b>What Narada knows</b></div>
      <p class="nx-mem-note">Things you have told Narada, kept between conversations. Nobody checked them: edit or remove anything that is wrong.</p>
      ${memError ? `<div class="nx-mem-err">${escHtml(memError)}</div>` : ''}
      ${pending}
      ${groups || (pending ? '' : '<div class="nx-mem-empty">Nothing yet. Tell Narada something about yourself, or add it here.</div>')}
      <form class="nx-mem-add" data-form="add">
        <input class="nx-mem-in" name="text" maxlength="200" placeholder="Add a fact, one sentence" aria-label="Add a fact"/>
        <button type="submit">Add</button>
      </form>
      ${archived}`;
  }

  async function memoryAct(what, id, form) {
    const mem = '/api/narada/memory';
    try {
      memError = '';
      if (what === 'open') { memOpen = true; memEditing = null; $('nx-info').classList.add('mem'); renderInfo(); return loadMemory(); }
      if (what === 'back') { memOpen = false; $('nx-info').classList.remove('mem'); return renderInfo(); }
      if (what === 'edit') { memEditing = id; return renderInfo(); }
      if (what === 'cancel') { memEditing = null; return renderInfo(); }
      if (what === 'remove') await G._apiFn('DELETE', `${mem}/${id}`);
      else if (what === 'purge') await G._apiFn('DELETE', `${mem}/${id}?forever=1`);
      else if (what === 'restore') await G._apiFn('POST', `${mem}/${id}/restore`);
      else if (what === 'confirm') await G._apiFn('POST', `${mem}/${id}/confirm`);
      else if (what === 'save') { await G._apiFn('PATCH', `${mem}/${id}`, { text: form.text.value.trim() }); memEditing = null; }
      else if (what === 'add') {
        const text = form.text.value.trim();
        if (!text) return;
        await G._apiFn('POST', mem, { text });
      }
      haptic('tap');
    } catch (e) {
      memError = (e && e.detail) || 'That did not work.';
    }
    return loadMemory();
  }

  // ── "i" panel: memory, models and usage ────────────────────
  const fmt = n => (n == null ? '—' : Number(n).toLocaleString());
  function renderInfo() {
    const box = $('nx-info-body');
    if (!box) return;
    if (memOpen) { box.innerHTML = memoryHtml(); return; }
    const nim = (infoData && infoData.nim) || {}, v = (infoData && infoData.voice) || {};
    const r = lastReply;
    const row = (k, val) => `<div class="nx-info-row"><span>${k}</span><b>${val}</b></div>`;
    const esc = x => String(x == null ? '' : x).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const used = v.character_limit ? `${fmt(v.characters_used)} / ${fmt(v.character_limit)}` : '—';
    const known = memData ? memData.facts.length : null, waiting = memData ? (memData.pending || []).length : 0;
    box.innerHTML = `
      <button type="button" class="nx-mem-open" data-mem="open">
        <span>What Narada knows</span>
        <b>${known == null ? '' : known + (known === 1 ? ' fact' : ' facts')}${waiting ? ' · ' + waiting + ' waiting' : ''} ›</b>
      </button>
      <div class="nx-info-h">Brain · NVIDIA NIM</div>
      ${row('Model', esc(nim.last_model || (nim.models || [])[0] || '—'))}
      ${row('Last response', nim.last_latency_s != null ? nim.last_latency_s.toFixed(1) + ' s' : '—')}
      ${row('Calls since start', fmt(nim.calls))}
      ${row('Tokens since start', fmt(nim.tokens_used))}
      <div class="nx-info-h">Voice · ElevenLabs</div>
      ${row('Speech model', esc(v.tts_model || '—'))}
      ${row('Voice', esc(v.voice || '—'))}
      ${row('Characters this month', used)}
      ${r ? `<div class="nx-info-h">Last reply</div>
        ${row('Answered by', esc(r.lane === 'agent' ? (r.model || 'NIM') : r.lane === 'unavailable' ? 'NIM unavailable' : r.lane))}
        ${row('Round trip', r.seconds.toFixed(1) + ' s')}
        ${(r.actions || []).length ? `<div class="nx-info-acts">${r.actions.map(a => `<span>${esc(a)}</span>`).join('')}</div>` : ''}` : ''}`;
  }
  async function toggleInfo(force) {
    infoOpen = typeof force === 'boolean' ? force : !infoOpen;
    $('nx-info').classList.toggle('open', infoOpen);
    $('nx-info-btn').setAttribute('aria-expanded', String(infoOpen));
    haptic('tap');
    if (!infoOpen) return;
    renderInfo();
    loadMemory();
    try { infoData = await G._apiFn('GET', '/api/narada/info'); renderInfo(); } catch (_) {}
  }

  // ── Wiring ─────────────────────────────────────────────────
  function init() {
    canvas = $('nx-glyph');
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    readColors();
    resize();
    new ResizeObserver(resize).observe(canvas);
    new MutationObserver(readColors).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    const page = $('page-narada');
    const toCanvas = e => { const r = canvas.getBoundingClientRect(); return { x: (e.clientX - r.left) * DPR, y: (e.clientY - r.top) * DPR }; };
    page.addEventListener('pointermove', e => { if (e.pointerType !== 'touch') pointer = toCanvas(e); });
    page.addEventListener('pointerleave', () => { pointer = null; });
    // Only the core is a button: tapping it starts or ends a conversation.
    canvas.addEventListener('click', e => {
      const p = toCanvas(e);
      if (Math.hypot(p.x - cx, p.y - cy) < Math.max(coreR * 2.2, 44 * DPR)) { haptic('tap'); toggleVoice(); }
      else if (infoOpen) toggleInfo(false);
    });

    const input = $('nx-input');
    input.addEventListener('focus', () => $('nx-dock').classList.add('focus'));
    input.addEventListener('blur', () => $('nx-dock').classList.remove('focus'));
    restore();
    // Memory: chips in the conversation, and the panel's buttons and forms.
    $('nx-log').addEventListener('click', e => {
      const btn = e.target.closest('[data-chip]'), card = btn && btn.closest('.nx-memchip');
      if (card) chipAction(card, btn.dataset.chip);
    });
    const infoBody = $('nx-info-body');
    infoBody.addEventListener('click', e => {
      const btn = e.target.closest('[data-mem]');
      if (!btn) return;
      const rowEl = btn.closest('[data-id]');
      memoryAct(btn.dataset.mem, rowEl && rowEl.dataset.id);
    });
    infoBody.addEventListener('submit', e => {
      const form = e.target.closest('[data-form]');
      if (!form) return;
      e.preventDefault();
      memoryAct(form.dataset.form === 'edit' ? 'save' : 'add', form.dataset.id, form);
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop(); else if (window.G && $('page-narada').classList.contains('active')) start();
    });
    _syncChrome();
  }

  function onNav(pageId) {
    if (pageId === 'narada') { requestAnimationFrame(() => { resize(); start(); measureDock(); }); }
    else stop();                               // a live conversation keeps going in the island
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { toggleVoice, stopVoice, submit, onNav, voiceActive, haptic, toggleInfo, toggleSheet, clearSession };
})();
window.N = N;

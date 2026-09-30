/* Narada: one page to talk or type to the house.

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
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    CW = Math.round(r.width * DPR);
    CH = Math.round(r.height * DPR);
    canvas.width = CW; canvas.height = CH;
    buildField(r);
  }

  // Concentric rings from the core to the farthest corner. Dot size falls
  // off with distance; ring spacing and dots per ring follow the size, so
  // neighbours stay a small, even gap apart at every radius.
  function buildField(rect) {
    const top = 64, bar = $('nx-bar'), caps = $('nx-captions');
    const barTop = bar ? bar.getBoundingClientRect().top - rect.top : rect.height - 120;
    const capTop = caps ? Math.min(caps.getBoundingClientRect().top - rect.top, barTop) : barTop;
    cx = CW / 2;
    cy = ((top + capTop) / 2) * DPR;
    Rfx = Math.max(60 * DPR, Math.min(CW / 2, (capTop - top) / 2 * DPR) * 0.98);
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
          rbin: new Uint8Array(n), dim: new Float32Array(n) };
    // Dots behind the captions fade back so the words stay readable.
    const cr = caps ? caps.getBoundingClientRect() : null;
    const feather = 70;
    for (let i = 0; i < n; i++) {
      const x = pts[i * 5], y = pts[i * 5 + 1], ang = pts[i * 5 + 2], rf = pts[i * 5 + 3];
      D.x[i] = x; D.y[i] = y; D.ang[i] = ang; D.rf[i] = rf; D.size[i] = pts[i * 5 + 4];
      const u = ((ang + Math.PI / 2) / (Math.PI * 2) % 1 + 1) % 1;
      D.bin[i] = Math.min(RAYS - 1, Math.floor(u * RAYS));
      D.rbin[i] = Math.min(RINGS - 1, Math.floor(Math.min(rf, 0.999) * RINGS));
      let dim = 1;
      if (cr && cr.height > 4) {
        // Distance outside the caption box, feathered so there is no hard edge.
        const px = x / DPR + rect.left, py = y / DPR + rect.top;
        const ox = Math.max(cr.left - px, 0, px - cr.right), oy = Math.max(cr.top - py, 0, py - cr.bottom);
        const out = Math.hypot(ox, oy);
        dim = 0.2 + 0.8 * clamp(out / feather);
      }
      D.dim[i] = dim;
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
      const alpha = clamp((0.1 + 0.9 * e) * D.dim[i]);
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
    const stage = $('nx-stage');
    if (stage) stage.dataset.state = state;
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

  // ── Captions ───────────────────────────────────────────────
  let capTimer = 0;
  function caption(who, text) {
    const el = $(who === 'you' ? 'nx-cap-you' : 'nx-cap-narada');
    const box = $('nx-captions');
    if (!el || !box) return;
    el.textContent = text || '';
    el.classList.remove('in'); void el.offsetWidth; el.classList.add('in');
    box.classList.remove('fade');
    clearTimeout(capTimer);
    capTimer = setTimeout(() => box.classList.add('fade'), 9000);
  }
  function clearExtra() { const x = $('nx-extra'); if (x) x.innerHTML = ''; }

  function typeReply(text) {
    const el = $('nx-cap-narada');
    if (!el) return;
    caption('narada', '');
    let i = 0;
    synthTalkUntil = performance.now() + Math.min(6000, 400 + text.length * 22);
    setState('talking');
    (function tick() {
      i = Math.min(i + 2, text.length);
      el.textContent = text.slice(0, i);
      if (i < text.length) requestAnimationFrame(tick);
    })();
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
          setState('idle');
          if (wasOn) haptic('stop');
        },
        onModeChange: ({ mode }) => {
          if (mode === 'speaking') { setState('talking'); haptic('talk'); }
          else if (voice === 'on') setState('listening');
        },
        onMessage: (m) => {
          const who = m.role || m.source;
          if (who === 'user') {
            clearExtra();
            caption('you', m.message);
            lastUserAt = performance.now();
            setState('thinking');
          } else if (m.message) {
            caption('narada', m.message);
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
    clearExtra();
    caption('you', text);
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
      typeReply(res.response || '…');
      // A drafted automation needs a Confirm button, so it stays in view;
      // what ran and which model answered go to the "i" panel.
      if (res.proposal && window.H && H.proposalHtml) $('nx-extra').innerHTML = H.proposalHtml(res.proposal);
      if (infoOpen) renderInfo();
    } catch (e) {
      caption('narada', '');
      voiceError((e && e.detail) || 'Connection error. Please try again.');
    } finally {
      busy = false;
    }
  }

  // ── "i" panel: models and usage ────────────────────────────
  const fmt = n => (n == null ? '—' : Number(n).toLocaleString());
  function renderInfo() {
    const box = $('nx-info-body');
    if (!box) return;
    const nim = (infoData && infoData.nim) || {}, v = (infoData && infoData.voice) || {};
    const r = lastReply;
    const row = (k, val) => `<div class="nx-info-row"><span>${k}</span><b>${val}</b></div>`;
    const esc = x => String(x == null ? '' : x).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const used = v.character_limit ? `${fmt(v.characters_used)} / ${fmt(v.character_limit)}` : '—';
    box.innerHTML = `
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
    input.addEventListener('focus', () => $('nx-bar').classList.add('focus'));
    input.addEventListener('blur', () => $('nx-bar').classList.remove('focus'));
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop(); else if (window.G && $('page-narada').classList.contains('active')) start();
    });
    _syncChrome();
  }

  function onNav(pageId) {
    if (pageId === 'narada') { requestAnimationFrame(() => { resize(); start(); }); }
    else stop();                               // a live conversation keeps going in the island
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { toggleVoice, stopVoice, submit, onNav, voiceActive, haptic, toggleInfo };
})();
window.N = N;

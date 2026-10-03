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
     working     two comets circle opposite ways while the planner builds
                 something; each finished step sends a ring outward
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
  let workPulse = -100;               // when the planner last finished a step (s)

  // ── Voice session ──────────────────────────────────────────
  let conv = null, voice = 'off';     // off | connecting | on
  let clientMod = null, busy = false, lastUserAt = 0;
  let lastReply = null, infoOpen = false, infoData = null, keptArtifacts = [];
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
      case 'working': {
        const d1 = angDist(D.ang[i], t * 2.3 - Math.PI / 2) + Math.abs(rf - 0.38) * 2.4;
        const d2 = angDist(D.ang[i], Math.PI / 2 - t * 1.7) + Math.abs(rf - 0.74) * 2.4;
        const age = t - workPulse;
        const ring = age < 2.4 ? Math.exp(-Math.abs(rf - age * 0.75) * 8) * Math.exp(-age * 1.1) : 0;
        e = Math.max(breath * 0.8, Math.exp(-d1 * 2.2) * 0.9, Math.exp(-d2 * 2.4) * 0.72, ring);
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
    const pulse = state === 'thinking' || state === 'working' ? 0.5 + 0.5 * Math.sin(t * 6)
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
    if (lbl) lbl.textContent = state === 'talking' ? 'Speaking' : state === 'thinking' ? 'Thinking' : state === 'working' ? 'Working' : 'Listening';
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
        working: status.dataset.work || 'Working',
        talking: 'Speaking',
        error: status.dataset.err || 'Something went wrong',
      }[state] || '';
    }
    if (window.G && G.setDI) {
      // Typed questions show the island's thinking pill; voice has its own.
      const hud = $('top-hud');
      if ((state === 'thinking' || state === 'working') && voice === 'off') G.setDI('thinking');
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
    for (const m of session) {
      if (m.who === 'artifact') artifactFrames([m.art], true);
      else bubble(m.who, m.text);
    }
    syncDock();
  }

  // ── Formatting a reply ─────────────────────────────────────
  // The models write Markdown (**bold**, lists, `code`). Shown raw it was a
  // row of asterisks. Everything is escaped first and only these few marks
  // become tags, so a reply can never put markup of its own on the page.
  const mdEsc = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function mdInline(text) {
    return mdEsc(text)
      .replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
      .replace(/__([^_\n]+)__/g, '<b>$1</b>')
      .replace(/(^|[\s(])\*([^*\s][^*\n]*?)\*(?=[\s).,;:!?]|$)/g, '$1<i>$2</i>')
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }
  // A table: a row of cells between pipes, then a row of dashes, then rows.
  // Without this a table came out as lines of pipes and dashes.
  const mdRow = line => /^\s*\|.*\|\s*$/.test(line);
  const mdRule = line => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line) && line.includes('-');
  // (An escaped \| stays in its cell; no lookbehind, which older Safari cannot parse.)
  const mdCells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').replace(/\\\|/g, '\u0000')
    .split('|').map(c => c.replace(/\u0000/g, '|').trim());
  function mdTable(rows) {
    const [head, ...body] = rows.map(mdCells);
    return '<div class="md-table"><table><thead><tr>' + head.map(c => `<th>${mdInline(c)}</th>`).join('')
      + '</tr></thead><tbody>' + body.map(r => '<tr>' + head.map((_, i) => `<td>${mdInline(r[i] || '')}</td>`).join('') + '</tr>').join('')
      + '</tbody></table></div>';
  }
  function mdHtml(text) {
    const out = [];
    let list = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    const lines = String(text || '').split('\n');
    for (let n = 0; n < lines.length; n++) {
      const raw = lines[n];
      if (mdRow(raw) && n + 1 < lines.length && mdRule(lines[n + 1])) {
        const rows = [raw];
        n += 2;
        while (n < lines.length && mdRow(lines[n])) rows.push(lines[n++]);
        n--;
        close(); out.push(mdTable(rows));
        continue;
      }
      const line = raw.replace(/\s+$/, '');
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/), number = line.match(/^\s*\d+[.)]\s+(.*)$/);
      const head = line.match(/^\s*#{1,4}\s+(.*)$/);
      if (bullet || number) {
        const kind = bullet ? 'ul' : 'ol';
        if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
        out.push(`<li>${mdInline((bullet || number)[1])}</li>`);
      } else if (head) {
        close(); out.push(`<p class="md-h">${mdInline(head[1])}</p>`);
      } else if (!line.trim()) {
        close();
      } else if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
        close();                               // a rule: the gap says enough
      } else {
        close(); out.push(`<p>${mdInline(line)}</p>`);
      }
    }
    close();
    return out.join('');
  }
  // The same reply as plain words: for the line above the bar and while it types.
  const mdPlain = text => String(text || '').split('\n').filter(l => !(mdRule(l) && l.includes('|')))
    .map(l => mdRow(l) ? mdCells(l).join('  ·  ') : l).join('\n')
    .replace(/\*\*|__|`/g, '').replace(/^\s*#{1,4}\s+/gm, '')
    .replace(/^\s*[-*]\s+/gm, '• ').replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '$1');
  function setReply(el, text) {
    el.classList.add('md');
    el.innerHTML = mdHtml(text);
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
    if (who === 'narada' && text) setReply(el, text); else el.textContent = text || '';
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
    if (peek) peek.textContent = last ? mdPlain(last.text).replace(/\s*\n\s*/g, ' ') : '';
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
    // Typed out as plain words, then set in its formatting: half a bold mark
    // or a list still being typed would flicker.
    const plain = mdPlain(text), step = Math.max(3, Math.ceil(plain.length / 240));
    let i = 0;
    synthTalkUntil = performance.now() + Math.min(6000, 400 + plain.length * 22);
    setState('talking');
    (function tick() {
      i = Math.min(i + step, plain.length);
      if (i < plain.length) { el.textContent = plain.slice(0, i); scrollLog(); requestAnimationFrame(tick); }
      else { setReply(el, text); scrollLog(); }
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
    closeFullArtifact();
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
            watchVoiceTurn();
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
      const res = await ask(text);
      workDone(res);
      lastReply = { ...res, seconds: (performance.now() - t0) / 1000 };
      say('narada', res.response || '…', true);
      // A drafted automation needs a Confirm button, so it goes into the
      // conversation; what ran and which model answered go to the "i" panel.
      if (res.proposal && window.H && H.proposalHtml) {
        const card = bubble('extra', '');
        if (card) { card.innerHTML = H.proposalHtml(res.proposal); scrollLog(); }
      }
      snapshots(res.images);
      artifactFrames(res.artifacts);
      confirmCards(res.confirm);
      memoryChips(res.memory);
      offerChip(res.offer);
      observationChip(res.observation);
      pollMemory();                       // routines the house noticed, facts kept after a pause
      if (infoOpen) renderInfo();
    } catch (e) {
      workDone(null);
      const msg = (e && e.detail) || 'Connection error. Please try again.';
      say('error', msg);
      voiceError(msg);
    } finally {
      busy = false;
    }
  }

  // One typed question. The answer is streamed so the steps show while Narada
  // works; a browser or proxy that will not stream gets the plain request.
  async function ask(text) {
    if (!G._apiStream || !window.ReadableStream) return G._apiFn('POST', '/api/chat', { message: text });
    let meta = null, reply = '', got = false;
    const onEvent = ev => {
      got = true;
      if (ev.type === 'progress') workEvent(ev.event);
      else if (ev.type === 'meta') meta = ev;
      else if (ev.type === 'token') reply += ev.text;
    };
    try {
      await G._apiStream('/api/chat/stream', { message: text }, onEvent);
    } catch (e) {
      if (e && e.status === 401 && !got) {
        await G._apiFn('GET', '/api/session');          // renews the sign-in, or signs out
        await G._apiStream('/api/chat/stream', { message: text }, onEvent);
      } else if (!got && !(e && e.status)) {
        return G._apiFn('POST', '/api/chat', { message: text });
      } else throw e;
    }
    if (!meta) throw { detail: 'The connection dropped before Narada answered. Please try again.' };
    return { ...meta, response: reply };
  }

  // ── Working: the planner's steps, as they happen ───────────
  // A request that needs something built goes to a slower, more capable
  // model. Its steps are listed in the conversation while it works, the dot
  // field changes to "working", and the bar under the status shows it is busy.
  const words = n => String(n || '').replace(/_/g, ' ');
  const shortModel = m => String(m || '').split('/').pop();
  let work = null;                    // {el, list, steps}

  function workCard(model) {
    if (work) return work;
    setPending(false);
    const el = bubble('extra', '');
    if (!el) return null;
    el.classList.add('nx-work');
    el.innerHTML = `<button type="button" class="nx-work-h" data-work="toggle" aria-expanded="true">
        <span class="nx-work-dot" aria-hidden="true"></span><span class="nx-work-t">Planning</span>
        <span class="nx-work-m">${escHtml(shortModel(model))}</span></button>
      <ol class="nx-work-l"></ol>`;
    work = { el, list: el.querySelector('.nx-work-l'), steps: 0 };
    if (!userMinimised) sheetOpen = true;
    syncDock();
    scrollLog();
    return work;
  }

  function workStatus(text) {
    const status = $('nx-status');
    if (status) status.dataset.work = text;
    if (state === 'working') _syncChrome(); else setState('working');
  }

  function workEvent(ev) {
    if (!ev) return;
    if (ev.type === 'lane' && ev.lane === 'planner') {
      workCard(ev.model);
      workStatus('Planning');
      haptic('tap');
      return;
    }
    if (ev.type !== 'step') return;
    if (!work) {                      // the quick model: no card, just say what it is doing
      const status = $('nx-status');
      if (status && ev.status === 'start') { status.dataset.work = words(ev.tool); }
      return;
    }
    if (ev.status === 'start') {
      const li = document.createElement('li');
      li.className = 'run';
      li.textContent = words(ev.tool);
      work.list.appendChild(li);
      workStatus('Working · ' + words(ev.tool));
    } else {
      const li = work.list.querySelector('li.run');
      if (li) li.className = ev.waiting ? 'wait' : ev.ok ? 'ok' : 'bad';
      work.steps++;
      workPulse = performance.now() / 1000;
      haptic('talk');
    }
    scrollLog();
  }

  function workDone(res) {
    const status = $('nx-status');
    if (status) delete status.dataset.work;
    if (!work) return;
    const w = work;
    work = null;
    w.list.querySelectorAll('li.run').forEach(li => { li.className = 'bad'; });
    w.el.classList.add('done', 'shut');
    const n = w.steps;
    w.el.querySelector('.nx-work-t').textContent = res ? `Planned in ${n} step${n === 1 ? '' : 's'}` : 'Stopped';
    w.el.querySelector('.nx-work-h').setAttribute('aria-expanded', 'false');
    if (!n) w.el.remove();
  }

  // A spoken turn's steps cannot come through the voice service, so the page
  // asks for them while it waits for the reply.
  let voiceWatch = 0;
  function watchVoiceTurn() {
    clearTimeout(voiceWatch);
    let seen = 0;
    const tick = async () => {
      if (!conv || (state !== 'thinking' && state !== 'working')) return;
      try {
        const p = (await G._apiFn('GET', '/api/narada/progress')).progress;
        if (p && p.lane === 'planner') {
          workStatus(p.step ? 'Working · ' + words(p.step) : 'Planning');
          if (p.done > seen) { seen = p.done; workPulse = performance.now() / 1000; }
        }
      } catch (_) {}
      voiceWatch = setTimeout(tick, 1500);
    };
    voiceWatch = setTimeout(tick, 1800);
  }

  // ── Cards: things Narada proposes and a person confirms ────
  // Deleting a device, changing who can sign in, saving a shortcut: Narada
  // only proposes these. The card says exactly what will happen; a password,
  // where one is needed, is typed here and never passes through the model.
  const showValue = v => typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v);
  function stepsHtml(lines) {
    return (lines || []).map(line => {
      const depth = (line.match(/^ */)[0].length / 2) | 0;
      return `<li style="--d:${depth}"${/:$/.test(line) ? ' class="branch"' : ''}>${escHtml(line.trim())}</li>`;
    }).join('');
  }
  function confirmCards(cards) {
    for (const c of cards || []) {
      const el = bubble('extra', '');
      if (!el) continue;
      el.classList.add('nx-card');
      el.dataset.action = c.id;
      const sc = c.shortcut;
      el.innerHTML = `<div class="nx-card-k">Needs your confirmation</div>
        <div class="nx-card-t">${escHtml(sc ? c.title + ': ' + sc.name : c.title)}</div>
        ${sc ? `<div class="nx-card-when">${escHtml(sc.when)}${sc.only_if ? `<small>Only if ${escHtml(sc.only_if)}</small>` : ''}</div>
                <ol class="nx-card-steps">${stepsHtml(sc.steps)}</ol>
                ${(sc.cautions || []).map(w => `<div class="nx-card-warn">${escHtml(w)}</div>`).join('')}` : `<div class="nx-card-about">${escHtml(c.about)}</div>`}
        ${(c.lines || []).length ? `<dl class="nx-card-lines">${c.lines.map(l => `<dt>${escHtml(l.name)}</dt><dd>${escHtml(showValue(l.value))}</dd>`).join('')}</dl>` : ''}
        ${(c.typed || []).map(f => `<label class="nx-card-f"><span>${escHtml(f.label)}${f.required ? '' : ' (leave empty to keep)'}</span>
            <input class="nx-card-in" type="${f.secret ? 'password' : 'text'}" data-field="${escHtml(f.name)}" autocomplete="new-password" maxlength="128"/></label>`).join('')}
        <div class="nx-card-b"><button type="button" data-card="confirm">Confirm</button><button type="button" data-card="cancel">Cancel</button><span class="nx-card-msg" role="status"></span></div>`;
    }
    if ((cards || []).length) { scrollLog(); haptic('tap'); }
  }

  async function cardAction(card, what) {
    const msg = card.querySelector('.nx-card-msg'), buttons = card.querySelectorAll('.nx-card-b button');
    const url = `/api/narada/actions/${encodeURIComponent(card.dataset.action)}/`;
    buttons.forEach(b => { b.disabled = true; });
    msg.textContent = '';
    try {
      if (what === 'cancel') {
        await G._apiFn('POST', url + 'cancel');
        card.classList.add('closed');
        msg.textContent = 'Cancelled';
      } else {
        const fields = {};
        card.querySelectorAll('[data-field]').forEach(i => { if (i.value) fields[i.dataset.field] = i.value; });
        await G._apiFn('POST', url + 'confirm', { fields });
        card.classList.add('closed', 'ok');
        msg.textContent = 'Done';
        card.querySelectorAll('[data-field]').forEach(i => { i.value = ''; i.disabled = true; });
        if (window.H && H.refresh) H.refresh();
      }
      card.querySelectorAll('.nx-card-b button').forEach(b => b.remove());
      haptic('tap');
    } catch (e) {
      msg.textContent = (e && e.detail) || 'That did not work';
      // A card that has expired cannot be tried again; anything else can.
      if (!/expired/i.test(msg.textContent)) buttons.forEach(b => { b.disabled = false; });
      haptic('error');
    }
  }

  // The camera's view, asked for in words. This page fetches the picture as
  // the signed-in person; the model never sees it.
  function snapshots(list) {
    for (const im of list || []) {
      if (im.kind !== 'snapshot') continue;
      const el = bubble('extra', '');
      if (!el) continue;
      el.classList.add('nx-shot');
      const auth = G._authQuery ? G._authQuery() : '';
      const img = new Image();
      img.alt = 'What the camera sees now';
      img.onload = scrollLog;
      img.onerror = () => { el.textContent = 'The camera has no picture to show right now.'; };
      img.src = `${G._base ? G._base() : ''}/api/snapshot?t=${Date.now()}${auth ? '&' + auth : ''}`;
      const cap = document.createElement('span');
      cap.textContent = 'Camera · ' + new Date((im.at || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      el.append(img, cap);
    }
    if ((list || []).length) scrollLog();
  }

  // ── Artifacts: pages Narada writes ─────────────────────────
  // Each is shown in a frame that has no access to this site. What the page
  // needs (live data, a button that switches something) it asks for by
  // message; this page makes the call as the signed-in person and answers.
  const ART_MIN = 80, ART_MAX = 720;
  function artifactFrames(list, restoring) {
    for (const a of list || []) {
      if (!a || !a.id || document.querySelector(`.nx-artifact[data-art="${a.id}"]`)) continue;
      const el = bubble('extra', '');
      if (!el) continue;
      el.classList.add('nx-artifact');
      el.classList.toggle('kept', !!a.pinned);
      el.dataset.art = a.id;
      const src = `${G._base ? G._base() : ''}/api/narada/artifacts/${encodeURIComponent(a.id)}/view?k=${encodeURIComponent(a.key)}`;
      el.innerHTML = `<div class="nx-art-h"><b>${escHtml(a.title)}</b>
          <span class="nx-art-acts"><button type="button" data-art-act="full">Expand</button><button type="button" data-art-act="keep">${a.pinned ? 'Kept' : 'Keep'}</button><button type="button" data-art-act="remove">Remove</button><button type="button" class="nx-art-x" data-art-act="close" aria-label="Close">\u00d7</button></span></div>
        <iframe class="nx-art-f" sandbox="allow-scripts" referrerpolicy="no-referrer" title="${escHtml(a.title)}" src="${escHtml(src)}"></iframe>`;
      if (!restoring) {
        session.push({ who: 'artifact', text: a.title, art: { id: a.id, key: a.key, title: a.title } });
        save();
      }
    }
    if ((list || []).length && !restoring) { syncDock(); scrollLog(); setTimeout(scrollLog, 700); }
  }

  // Expanded, an artifact goes into a layer of its own on top of the whole
  // page. It used to stay inside Narada's panel, which (a transformed
  // ancestor) held its "fixed" box to the panel's size, over the conversation,
  // with a 12 px "Close" link as the only way out; where it did reach the top
  // of the screen, the status bar sat over that link. Now: a large ×, Escape,
  // a tap outside it, or the phone's back gesture.
  let artOverlay = null;
  function openFullArtifact(card) {
    if (artOverlay) closeFullArtifact();
    const slot = document.createElement('div');
    slot.className = 'nx-art-slot';
    card.before(slot);
    const layer = document.createElement('div');
    layer.className = 'nx-art-overlay';
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.appendChild(card);
    document.body.appendChild(layer);
    card.classList.add('full');
    const btn = card.querySelector('[data-art-act="full"]');
    if (btn) btn.textContent = 'Shrink';
    const onKey = e => { if (e.key === 'Escape') { e.preventDefault(); closeFullArtifact(); } };
    const onPop = () => closeFullArtifact(true);
    layer.addEventListener('click', e => {
      if (e.target === layer) return closeFullArtifact();            // the dimmed space around it
      const act = e.target.closest('[data-art-act]');
      if (act) artifactAction(card, act.dataset.artAct, act);
    });
    document.addEventListener('keydown', onKey);
    try { history.pushState({ nxArtifact: card.dataset.art }, ''); window.addEventListener('popstate', onPop); } catch (_) {}
    artOverlay = { card, slot, layer, onKey, onPop };
    card.querySelector('.nx-art-x')?.focus();
  }
  function closeFullArtifact(fromHistory) {
    const o = artOverlay;
    if (!o) return;
    artOverlay = null;
    document.removeEventListener('keydown', o.onKey);
    window.removeEventListener('popstate', o.onPop);
    if (!fromHistory && history.state && history.state.nxArtifact) { try { history.back(); } catch (_) {} }
    o.card.classList.remove('full');
    const btn = o.card.querySelector('[data-art-act="full"]');
    if (btn) btn.textContent = 'Expand';
    if (o.slot.isConnected) o.slot.replaceWith(o.card); else o.card.remove();
    o.layer.remove();
  }

  async function artifactAction(card, what, btn) {
    const id = card.dataset.art;
    if (what === 'full') {
      if (card.classList.contains('full')) closeFullArtifact(); else openFullArtifact(card);
      return;
    }
    if (what === 'close') {
      if (card.classList.contains('full')) closeFullArtifact();
      return;
    }
    try {
      if (what === 'keep') {
        const keep = !card.classList.contains('kept');
        await G._apiFn('POST', `/api/narada/artifacts/${id}/pin`, { pinned: keep });
        card.classList.toggle('kept', keep);
        btn.textContent = keep ? 'Kept' : 'Keep';
      } else if (what === 'remove') {
        await G._apiFn('DELETE', `/api/narada/artifacts/${id}`).catch(() => {});
        session = session.filter(m => !(m.who === 'artifact' && m.art.id === id));
        save();
        if (card.classList.contains('full')) closeFullArtifact();
        card.remove();
        syncDock();
      }
      haptic('tap');
    } catch (e) {
      if (window.G) G.showToast((e && e.detail) || 'That did not work', 'error');
    }
  }

  function onArtifactMessage(e) {
    const m = e.data;
    if (!m || m.garuda !== true) return;
    const frame = [...document.querySelectorAll('.nx-art-f')].find(f => f.contentWindow === e.source);
    if (!frame) return;
    const card = frame.closest('.nx-artifact');
    if (m.type === 'resize') {
      const h = Math.max(ART_MIN, Math.min(ART_MAX, Number(m.height) || 0));
      // The newest thing in the conversation stays in view as it finds its height.
      const log = $('nx-log'), follow = log && card === log.lastElementChild;
      frame.style.height = h + 'px';
      if (follow) setTimeout(scrollLog, 320);
      return;
    }
    if (m.type !== 'call') return;
    const answer = extra => frame.contentWindow && frame.contentWindow.postMessage({ garuda: true, type: 'result', id: m.id, ...extra }, '*');
    G._apiFn('POST', `/api/narada/artifacts/${card.dataset.art}/call`, { capability: String(m.capability || ''), args: m.args && typeof m.args === 'object' ? m.args : {} })
      .then(res => answer({ result: res.result }))
      .catch(err => answer({ error: (err && err.detail) || 'That did not work' }));
  }

  // ── Memory: what Narada knows about the household ──────────
  // Every change memory makes shows as a chip in the conversation ("Saved to
  // memory", with Undo). The whole memory is one tap away in the "i" panel.
  const escHtml = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const CHIP_LABEL = { saved: 'Saved to memory', updated: 'Memory updated', forgotten: 'Removed from memory', pending: 'Keep this in memory?' };
  const ORIGIN_LABEL = { asked: 'you asked', noticed: 'picked up in conversation', distilled: 'from a past conversation', manual: 'added by hand', observed: 'noticed from how the house is used', choice: 'your choice' };
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

  // A routine the house has noticed, offered once in a while: Yes makes it a
  // schedule, No leaves it to you. Either way Narada remembers the answer.
  function offerChip(offer) {
    if (!offer || document.querySelector(`.nx-memchip[data-offer="${offer.id}"]`)) return;
    const card = bubble('extra', '');
    if (!card) return;
    card.classList.add('nx-memchip');
    card.dataset.offer = offer.id;
    card.innerHTML = `<span class="nx-memchip-l">Noticed</span><span class="nx-memchip-t">${escHtml(offer.text)}</span>`
      + '<span class="nx-memchip-b"><button type="button" data-chip="accept">Yes</button><button type="button" data-chip="decline">No</button></span>';
    scrollLog();
  }

  // Something Narada noticed that nobody asked about. The label is the name of
  // the rule that fired, so it can be judged; "Don't tell me this" silences it.
  function observationChip(o) {
    if (!o || document.querySelector(`.nx-memchip[data-observation="${o.key}"]`)) return;
    const card = bubble('extra', '');
    if (!card) return;
    card.classList.add('nx-memchip');
    card.dataset.observation = o.key;
    card.innerHTML = `<span class="nx-memchip-l">Noticed · ${escHtml(o.title)}</span><span class="nx-memchip-t">${escHtml(o.text)}</span>`
      + '<span class="nx-memchip-b"><button type="button" data-chip="ok">OK</button><button type="button" data-chip="mute">Don’t tell me this</button></span>';
    scrollLog();
  }

  async function observationAction(card, what) {
    const b = card.querySelector('.nx-memchip-b');
    card.querySelectorAll('button').forEach(x => { x.disabled = true; });
    if (what !== 'mute') { b.textContent = ''; return; }
    try {
      await G._apiFn('POST', '/api/narada/observations/mute', { key: card.dataset.observation });
      b.textContent = 'I will not mention it again';
      haptic('tap');
      pollMemory();
    } catch (e) {
      b.textContent = (e && e.detail) || 'That did not work';
    }
  }

  async function offerAction(card, what) {
    const b = card.querySelector('.nx-memchip-b'), l = card.querySelector('.nx-memchip-l');
    card.querySelectorAll('button').forEach(x => { x.disabled = true; });
    try {
      await G._apiFn('POST', `/api/home/suggestions/${encodeURIComponent(card.dataset.offer)}/${what === 'accept' ? 'accept' : 'dismiss'}`);
      l.textContent = what === 'accept' ? 'Scheduled' : 'Left to you';
      b.textContent = '';
      haptic('tap');
      pollMemory();                       // the answer itself is remembered: show its chip
    } catch (e) {
      b.textContent = (e && e.detail) || 'That did not work';
    }
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
      ${keptArtifacts.length ? `<div class="nx-info-h">Kept artifacts</div>` + keptArtifacts.map(a =>
        `<button type="button" class="nx-art-open" data-art-open="${esc(a.id)}"><span>${esc(a.title)}</span><b>Show ›</b></button>`).join('') : ''}
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
        ${row('Answered by', esc(r.lane === 'agent' ? (r.model || 'NIM') + (r.planner ? ' · planner' : '') : r.lane === 'unavailable' ? 'NIM unavailable' : r.lane))}
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
    try {
      keptArtifacts = ((await G._apiFn('GET', '/api/narada/artifacts')).artifacts || []).filter(a => a.pinned);
      renderInfo();
    } catch (_) {}
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
      const act = e.target.closest('[data-card], [data-art-act], [data-work]');
      if (act) {
        if (act.dataset.card) return cardAction(act.closest('.nx-card'), act.dataset.card);
        if (act.dataset.artAct) return artifactAction(act.closest('.nx-artifact'), act.dataset.artAct, act);
        const box = act.closest('.nx-work');
        act.setAttribute('aria-expanded', String(box.classList.toggle('shut') === false));
        return;
      }
      const btn = e.target.closest('[data-chip]'), card = btn && btn.closest('.nx-memchip');
      if (!card) return;
      if (card.dataset.offer) offerAction(card, btn.dataset.chip);
      else if (card.dataset.observation) observationAction(card, btn.dataset.chip);
      else chipAction(card, btn.dataset.chip);
    });
    const infoBody = $('nx-info-body');
    infoBody.addEventListener('click', e => {
      const open = e.target.closest('[data-art-open]');
      if (open) {                       // a kept page, shown again in the conversation
        const a = keptArtifacts.find(x => x.id === open.dataset.artOpen);
        if (a) { artifactFrames([a]); sheetOpen = true; userMinimised = false; syncDock(); toggleInfo(false); scrollLog(); }
        return;
      }
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
    window.addEventListener('message', onArtifactMessage);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop(); else if (window.G && $('page-narada').classList.contains('active')) start();
    });
    _syncChrome();
  }

  function onNav(pageId) {
    if (pageId !== 'narada') closeFullArtifact();
    // Facts kept after a conversation went quiet show their chip the next time the page is open.
    if (pageId === 'narada') { requestAnimationFrame(() => { resize(); start(); measureDock(); }); pollMemory(); }
    else stop();                               // a live conversation keeps going in the island
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { toggleVoice, stopVoice, submit, onNav, voiceActive, haptic, toggleInfo, toggleSheet, clearSession,
           closeArtifact: () => closeFullArtifact() };
})();
window.N = N;

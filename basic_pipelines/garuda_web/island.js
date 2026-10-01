/* The Dynamic Island, beyond a status pill.

   app.js still owns the states it always had (alert, night presence, all
   clear, Narada thinking, voice). This adds what makes an island an island:

     shape         one black shape with continuous (squircle) corners, cut by
                   a clip-path this file redraws every frame from a spring.
                   Width, height and corner radius share the spring, so a
                   change of mind mid-flight carries its momentum, as on iOS
     press         it squashes under the pointer and swells on hover
     open          a click grows it into a panel: what the house is doing,
                   the clock, figures and controls that fit the page you are on
     pages         each page gives the resting pill its own glyph, a small
                   animation of what that page is for, and a live figure
     activities    things that are going on get a place in the pill:
                   a clip being recorded (with a running timer), being offline
     notices       a change slides through for a moment and leaves:
                   "Do Not Disturb on", "Back online", "Clip saved"

   Everything here is driven from the state the server already pushes
   (DI.onState, called from app.js tick) and uses G's own functions to act,
   so the island never holds a second copy of the truth. Each state asks for
   a pill width through --di-w in style.css; this file only reads it. Phones
   hide the island (style.css); this file is then idle. */
const DI = (() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const MODES = [
    { key: 'privacy', label: 'Privacy',        on: 'Privacy blur on',    off: 'Privacy blur off',    icon: 'privacy' },
    { key: 'night',   label: 'Night',          on: 'Night mode on',      off: 'Night mode off',      icon: 'night-mode' },
    { key: 'dnd',     label: 'Do Not Disturb', on: 'Do Not Disturb on',  off: 'Do Not Disturb off',  icon: 'dnd' },
    { key: 'idle',    label: 'Idle',           on: 'Idle on',            off: 'Idle off',            icon: 'idle' },
  ];
  const EXTRA = { email_off: ['Email alerts off', 'Email alerts on', 'email-off'],
                  emergency: ['Emergency on', 'Emergency off', 'emergency'] };

  const icon = name => `<span class="gi gi-${name}" aria-hidden="true"></span>`;
  // A page glyph: a 16 px drawing animated in CSS (.dg-* in style.css).
  const glyph = (name, parts = 0, inner = '') => `<i class="dg dg-${name}" aria-hidden="true">${'<b></b>'.repeat(parts)}${inner}</i>`;
  const stroke = d => `<svg viewBox="0 0 16 16">${d}</svg>`;

  // ── what each page shows ───────────────────────────────────
  const home = s => (s && s.home && !s.home.error && s.home.devices != null) ? s.home : null;
  const ST = {
    uptime:   s => ['Uptime', s.uptime_seconds != null ? fmtUptime(s.uptime_seconds + (Date.now() - s._at) / 1000) : '—'],
    last:     s => ['Last alert', s.last_alert ? ago(s.last_alert) : 'Never'],
    net:      s => ['Network', s.net_online === false ? 'Offline' : 'Online'],
    on:       s => ['Switched on', home(s) ? `${home(s).devices_on} of ${home(s).devices}` : '—'],
    presence: s => ['Presence', home(s) ? (home(s).owner_presence === 'away' ? 'Away' : 'Home') : '—'],
    confirm:  s => ['To confirm', home(s) ? String(home(s).pending_proposals || 0) : '—'],
    voice:    () => ['Voice', window.N && N.voiceActive() ? 'Live' : 'Ready'],
    logs:     s => ['Log lines', String((s.system_log || []).length)],
  };
  const go = (label, ic, page) => ({ label, icon: ic, run: () => window.G && G.nav(page) });
  const DEFAULT_STATS = ['uptime', 'last', 'net'];
  const PAGES = {
    dashboard:    { glyph: glyph('radar', 1) },
    narada:       { glyph: glyph('dots', 5), stats: ['voice', 'net', 'uptime'] },
    devices:      { glyph: glyph('sw', 1), stats: ['on', 'presence', 'confirm'],
                    val: s => home(s) ? `${home(s).devices_on} on` : '',
                    links: [{ label: 'All off', icon: 'power', run: () => window.H && H.allOff() }, go('Automations', 'automate', 'auto')] },
    auto:         { glyph: glyph('clock', 1), stats: ['confirm', 'presence', 'on'],
                    val: s => home(s) && home(s).pending_proposals ? `${home(s).pending_proposals} new` : '',
                    links: [go('Devices', 'devices', 'devices'), go('Insights', 'insights', 'insights')] },
    insights:     { glyph: glyph('bars', 4), stats: ['on', 'last', 'uptime'],
                    links: [go('Devices', 'devices', 'devices'), go('Automations', 'automate', 'auto')] },
    'a-email':    { glyph: glyph('mail', 0, stroke('<rect x="1.5" y="3.5" width="13" height="9" rx="2"/><path class="dg-draw" pathLength="1" d="M2.6 5.2l5.4 4 5.4-4"/>')) },
    'a-settings': { glyph: glyph('sliders', 2) },
    'a-logs':     { glyph: glyph('lines', 3), stats: ['logs', 'last', 'uptime'] },
    'a-cmds':     { glyph: glyph('prompt', 1, stroke('<path d="M2.5 4.5l4 3.5-4 3.5"/>')) },
  };

  let hud, shell, pill, rest, ring, panel, last = null, open = false, page = 'dashboard';
  let noticeTimer = 0, clockTimer = 0, recTimer = 0, recSince = 0, swapTimer = 0;
  let wasOnline = true, wasRecording = false;
  const held = {};                  // mode chips just tapped: key -> time, so a stale push cannot flip them back

  // ── the shape ──────────────────────────────────────────────
  // Every corner is a superellipse, never a plain arc: an arc meets a straight
  // edge with a jump in curvature, which the eye reads as a kink. Here the
  // curve eases into the edge. Each corner reaches up to 1.53 radii along an
  // edge (Apple's continuous corner); on the pill the height leaves no room
  // for that, so the ends stretch along the width instead.
  const STEPS = 18, COS = [], SIN = [];
  for (let i = 0; i <= STEPS; i++) { const t = i / STEPS * Math.PI / 2; COS.push(Math.cos(t)); SIN.push(Math.sin(t)); }
  function outline(x, w, h, r) {
    r = Math.max(1, Math.min(r, w / 2, h / 2));
    const rx = Math.min(1.53 * r, w / 2), ry = Math.min(1.53 * r, h / 2);
    const e = 2 / (2 + 1.27 * ((rx + ry) / r - 2) / 1.06);
    const dx = [], dy = [], f = v => v.toFixed(2);
    for (let i = 0; i <= STEPS; i++) { dx.push(rx * (1 - Math.pow(COS[i], e))); dy.push(ry * (1 - Math.pow(SIN[i], e))); }
    let d = '';
    for (let i = 0; i <= STEPS; i++) d += `${i ? 'L' : 'M'}${f(x + dx[i])} ${f(dy[i])}`;
    for (let i = STEPS; i >= 0; i--) d += `L${f(x + w - dx[i])} ${f(dy[i])}`;
    for (let i = 0; i <= STEPS; i++) d += `L${f(x + w - dx[i])} ${f(h - dy[i])}`;
    for (let i = STEPS; i >= 0; i--) d += `L${f(x + dx[i])} ${f(h - dy[i])}`;
    return d + 'Z';
  }

  const PILL_H = 37, PILL_R = 18.5, OPEN_R = 30, SLACK = 24;   // SLACK: room in the shell for the spring to overshoot
  const cur = { w: 126, h: PILL_H, r: PILL_R }, vel = { w: 0, h: 0, r: 0 }, to = { w: 126, h: PILL_H, r: PILL_R };
  let raf = 0, lastT = 0, stiff = 260, damp = 21, shellW = 400;
  let closing = false;              // true from a close until the shape has come to rest
  const still = window.matchMedia('(prefers-reduced-motion: reduce)');

  function paint() {
    const d = outline((shellW - cur.w) / 2, cur.w, cur.h, cur.r);
    shell.style.clipPath = `path("${d}")`;
    ring.setAttribute('d', d);
    pill.style.width = cur.w.toFixed(1) + 'px';
  }
  function frame(t) {
    const dt = Math.min(0.034, Math.max(0.001, (t - lastT) / 1000));
    lastT = t;
    for (let n = Math.ceil(dt / 0.004), h = dt / n; n > 0; n--) {
      for (const k in cur) { vel[k] += (stiff * (to[k] - cur[k]) - damp * vel[k]) * h; cur[k] += vel[k] * h; }
    }
    let rested = true;
    for (const k in cur) if (Math.abs(to[k] - cur[k]) > 0.08 || Math.abs(vel[k]) > 1) rested = false;
    if (rested) { for (const k in cur) { cur[k] = to[k]; vel[k] = 0; } raf = 0; closing = false; }
    else raf = requestAnimationFrame(frame);
    paint();
  }
  function spring(k, c) {
    stiff = k; damp = c;
    if (still.matches || document.hidden) { for (const key in cur) { cur[key] = to[key]; vel[key] = 0; } closing = false; paint(); return; }
    if (!raf) { lastT = performance.now(); raf = requestAnimationFrame(frame); }
  }

  function retarget() {
    if (!shell) return;
    if (open) {
      const h = panel.offsetHeight;
      shell.style.height = (h + SLACK) + 'px';
      to.w = shellW - SLACK; to.h = h; to.r = OPEN_R;
      spring(210, 21);                    // a little past, then home
    } else {
      to.w = parseFloat(getComputedStyle(hud).getPropertyValue('--di-w')) || 126;
      to.h = PILL_H; to.r = PILL_R;
      if (closing) spring(330, 33);       // closing is quicker and barely bounces
      else spring(260, 20);               // a pill changing width is the playful one
    }
  }
  function measure() {
    if (!shell.offsetWidth) return;                 // not on screen (signed out, or a phone)
    shellW = shell.offsetWidth;
    const w = rest.scrollWidth;
    if (w) hud.style.setProperty('--di-rest-w', Math.max(96, 2 * Math.ceil((w + 34) / 2)) + 'px');
    retarget();
  }

  // ── build ──────────────────────────────────────────────────
  function build() {
    hud = $('top-hud'); shell = $('di-shell'); pill = $('di-pill'); rest = $('hud-rest');
    const live = $('di-live');
    if (!hud || !shell || !pill || !rest || !live || hud.dataset.di) return false;
    hud.dataset.di = '1';
    live.insertAdjacentHTML('beforeend', `
      <div class="di-cnt di-cnt-notice"><span id="di-notice-ic"></span><span class="di-lbl" id="di-notice-lbl"></span></div>
      <div class="di-cnt di-cnt-rec"><span class="di-pip di-r"></span><span class="di-lbl">Recording</span><span class="di-grow"></span><span class="di-lbl di-num" id="di-rec-t">0:00</span></div>
      <div class="di-cnt di-cnt-offline">${icon('sensor')}<span class="di-lbl">Offline</span></div>`);
    shell.insertAdjacentHTML('beforeend', `
      <div class="di-panel" id="di-panel" role="dialog" aria-label="System status">
        <div class="di-p-eyebrow">
          <span id="di-p-glyph"></span><span id="di-p-page"></span>
          <span class="di-grow"></span><span class="di-p-clock di-num" id="di-p-clock"></span>
        </div>
        <div class="di-p-head">
          <span class="di-p-ic" id="di-p-ic"></span>
          <div class="di-p-title"><b id="di-p-state">All clear</b><span id="di-p-desc">No activity detected</span></div>
        </div>
        <div class="di-p-stats" id="di-p-stats">${[0, 1, 2].map(() => '<div><span></span><b class="di-num"></b></div>').join('')}</div>
        <div class="di-p-modes" id="di-p-modes">${MODES.map(m =>
          `<button type="button" class="di-chip" data-mode="${m.key}" aria-pressed="false">${icon(m.icon)}<span>${m.label}</span></button>`).join('')}</div>
        <div class="di-p-modes" id="di-p-links" hidden></div>
        <div class="di-p-acts">
          <button type="button" class="di-act di-act-main" id="di-p-talk">${icon('mic')}<span>Talk to Narada</span></button>
          <button type="button" class="di-act" id="di-p-cam" aria-label="Open the camera">${icon('camera')}</button>
          <button type="button" class="di-act" id="di-p-close" aria-label="Close">${icon('minimise')}</button>
        </div>
      </div>
      <svg class="di-ring" aria-hidden="true"><path id="di-ring-path"/></svg>`);
    panel = $('di-panel'); ring = $('di-ring-path');
    shell.setAttribute('role', 'button');
    shell.setAttribute('tabindex', '0');
    shell.setAttribute('aria-expanded', 'false');
    shell.setAttribute('aria-label', 'System status');

    shell.addEventListener('click', e => { if (!open) { e.stopPropagation(); setOpen(true); } });
    shell.addEventListener('keydown', e => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target === shell) { e.preventDefault(); setOpen(!open); }
    });
    document.addEventListener('click', e => { if (open && !shell.contains(e.target)) setOpen(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && open) setOpen(false); });
    panel.addEventListener('click', onPanelClick);

    // app.js and narada.js change the pill by switching classes; follow them.
    new MutationObserver(retarget).observe(hud, { attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', measure);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure);
    setPage(page, false);
    for (const k in cur) cur[k] = to[k];
    paint();
    return true;
  }

  function onPanelClick(e) {
    e.stopPropagation();
    const chip = e.target.closest('.di-chip');
    if (chip && chip.dataset.mode && window.G) {
      const on = chip.getAttribute('aria-pressed') === 'true';
      chip.setAttribute('aria-pressed', String(!on));      // answers the finger at once; the server confirms
      held[chip.dataset.mode] = Date.now();
      G.toggleMode(chip.dataset.mode, on);
      return;
    }
    if (chip && chip.dataset.link) {
      const link = (PAGES[page].links || [])[+chip.dataset.link];
      setOpen(false);
      if (link) link.run();
      return;
    }
    const act = e.target.closest('.di-act');
    if (!act) return;
    setOpen(false);
    if (act.id === 'di-p-talk') {
      if (window.G) G.nav('narada');
      if (window.N) N.toggleVoice();
    } else if (act.id === 'di-p-cam') {
      if (window.G) G.nav('dashboard');
    }
  }

  // ── open / close ───────────────────────────────────────────
  function setOpen(v) {
    if (!hud || open === v) return;
    open = v;
    closing = !open;
    clearInterval(clockTimer);
    if (open) {
      clearTimeout(noticeTimer);
      hud.classList.remove('di-notice');
      render();
      clockTimer = setInterval(render, 1000);
    }
    hud.classList.toggle('di-open', open);        // the class change retargets the spring
    shell.setAttribute('aria-expanded', String(open));
    retarget();
    if (navigator.vibrate) { try { navigator.vibrate(open ? [8, 40, 12] : 8); } catch (_) {} }
  }

  function fmtUptime(sec) {
    sec = Math.max(0, Math.floor(sec));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${sec % 60}s`;
  }
  function ago(iso) {
    const t = Date.parse(iso);
    if (!t) return 'Never';
    const s = Math.max(0, (Date.now() - t) / 1000);
    return s < 60 ? 'Just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
  }
  const setText = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };

  function render() {
    if (!panel) return;
    const s = last || { _at: Date.now() }, cfg = PAGES[page] || PAGES.dashboard;
    const talking = !!(window.N && N.voiceActive());
    const state = s.alert_active ? ['stop', 'Alert', s.danger_info || 'Threat detected', 'alert']
      : s.night_presence_alert ? ['night-mode', 'Night presence', 'Someone is moving at night', 'warn']
      : ['all-clear', 'All clear', 'No activity detected', ''];
    if (panel.dataset.icon !== state[0]) { panel.dataset.icon = state[0]; $('di-p-ic').innerHTML = icon(state[0]); }
    setText($('di-p-state'), state[1]);
    setText($('di-p-desc'), state[2]);
    panel.dataset.tone = state[3];
    setText($('di-p-clock'), new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    (cfg.stats || DEFAULT_STATS).forEach((key, i) => {
      const cell = $('di-p-stats').children[i], [label, value] = ST[key](s);
      setText(cell.firstElementChild, label);
      setText(cell.lastElementChild, value);
    });
    const modes = s.modes || {}, now = Date.now();
    panel.querySelectorAll('.di-chip[data-mode]').forEach(c => {
      const key = c.dataset.mode, want = String(!!modes[key]);
      if (held[key] && now - held[key] < 1500 && c.getAttribute('aria-pressed') !== want) return;
      delete held[key];
      c.setAttribute('aria-pressed', want);
    });
    setText($('di-p-talk').lastElementChild, talking ? 'End conversation' : 'Talk to Narada');
  }

  // ── pages ──────────────────────────────────────────────────
  function setPage(id, animate) {
    page = PAGES[id] ? id : 'dashboard';
    const cfg = PAGES[page];
    hud.dataset.page = page;
    $('di-glyph').innerHTML = cfg.glyph;
    setText($('di-val'), cfg.val && last ? cfg.val(last) : '');
    if (panel) {
      $('di-p-glyph').innerHTML = cfg.glyph;
      setText($('di-p-page'), $('hud-brand').textContent);
      const links = $('di-p-links');
      links.hidden = !cfg.links;
      $('di-p-modes').hidden = !!cfg.links;
      links.innerHTML = (cfg.links || []).map((l, i) =>
        `<button type="button" class="di-chip" data-link="${i}">${icon(l.icon)}<span>${l.label}</span></button>`).join('');
    }
    if (animate) {                                  // the old name leaves, the new one lands
      rest.classList.remove('di-swap'); void rest.offsetWidth; rest.classList.add('di-swap');
      clearTimeout(swapTimer);
      swapTimer = setTimeout(() => rest.classList.remove('di-swap'), 600);
    }
    measure();
    if (open) { render(); retarget(); }
  }
  function onNav(id) { if (hud || build()) setPage(id, true); }

  // ── notices: pass through the pill and leave ───────────────
  function notify(iconName, text, ms = 2600) {
    if (!hud || open) return;                       // an open panel already shows the change
    $('di-notice-ic').innerHTML = iconName ? icon(iconName) : '';
    const lbl = $('di-notice-lbl');
    lbl.textContent = text;
    hud.classList.add('di-notice');
    // As wide as the words, measured rather than guessed.
    const w = lbl.offsetWidth + (iconName ? 23 : 0) + 34;
    hud.style.setProperty('--di-notice-w', Math.min(320, 2 * Math.ceil(w / 2)) + 'px');
    shell.classList.remove('di-bump'); void shell.offsetWidth; shell.classList.add('di-bump');
    retarget();
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => hud.classList.remove('di-notice'), ms);
  }

  // ── state from the server ──────────────────────────────────
  function onState(s) {
    if (!s || typeof s !== 'object' || (!hud && !build())) return;
    const prev = last;
    last = Object.assign({}, s, { _at: Date.now() });

    if (prev) {
      const was = prev.modes || {}, now = s.modes || {};
      for (const m of MODES) {
        if (!!was[m.key] !== !!now[m.key]) notify(m.icon, now[m.key] ? m.on : m.off);
      }
      for (const key in EXTRA) {
        if (!!was[key] !== !!now[key]) notify(EXTRA[key][2], EXTRA[key][now[key] ? 0 : 1]);
      }
      if (s.alert_active && !prev.alert_active) {      // an alert arrives with a jolt
        shell.classList.remove('di-shake'); void shell.offsetWidth; shell.classList.add('di-shake');
      }
    }

    const online = s.net_online !== false;
    hud.classList.toggle('di-offline', !online);
    if (prev && online !== wasOnline) notify('sensor', online ? 'Back online' : 'Connection lost');
    wasOnline = online;

    const rec = s.clip_recording === true;
    if (rec && !wasRecording) {
      recSince = Date.now();
      clearInterval(recTimer);
      recTimer = setInterval(() => {
        const t = Math.floor((Date.now() - recSince) / 1000), el = $('di-rec-t');
        if (el) el.textContent = `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
      }, 500);
    } else if (!rec && wasRecording) {
      clearInterval(recTimer);
      notify('snapshot', 'Clip saved');
    }
    hud.classList.toggle('di-rec', rec);
    wasRecording = rec;

    // The page's live figure ("3 on"); the pill is re-measured when it changes.
    const cfg = PAGES[page], val = $('di-val'), text = cfg.val ? cfg.val(last) : '';
    if (val.textContent !== text || !hud.style.getPropertyValue('--di-rest-w')) { val.textContent = text; measure(); }

    if (open) render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();

  return { onState, onNav, notify, open: () => setOpen(true), close: () => setOpen(false) };
})();
window.DI = DI;

/* The Dynamic Island, beyond a status pill.

   app.js still owns the states it always had (alert, night presence, all
   clear, Narada thinking, voice). This adds what makes an island an island:

     press         it squashes under the pointer and swells on hover
     open          a click grows it into a panel: what the house is doing,
                   the clock, uptime, the modes as switches, and Narada
     activities    things that are going on get a place in the pill:
                   a clip being recorded (with a running timer), being offline
     notices       a change slides through for a moment and leaves:
                   "Do Not Disturb on", "Back online", "Clip saved"

   Everything here is driven from the state the server already pushes
   (DI.onState, called from app.js tick) and uses G's own functions to act,
   so the island never holds a second copy of the truth. Phones hide the
   island (style.css); this file is then idle. */
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

  let hud, panel, last = null, open = false;
  let noticeTimer = 0, clockTimer = 0, recTimer = 0, recSince = 0;
  let wasOnline = true, wasRecording = false;

  const esc = x => String(x == null ? '' : x).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const icon = name => `<span class="gi gi-${name}" aria-hidden="true"></span>`;

  function build() {
    hud = $('top-hud');
    const live = $('di-live');
    if (!hud || !live || hud.dataset.di) return false;
    hud.dataset.di = '1';
    live.insertAdjacentHTML('beforeend', `
      <div class="di-cnt di-cnt-notice"><span id="di-notice-ic"></span><span class="di-lbl" id="di-notice-lbl"></span></div>
      <div class="di-cnt di-cnt-rec"><span class="di-pip di-r"></span><span class="di-lbl">Recording</span><span class="di-grow"></span><span class="di-lbl di-num" id="di-rec-t">0:00</span></div>
      <div class="di-cnt di-cnt-offline">${icon('sensor')}<span class="di-lbl">Offline</span></div>`);
    hud.insertAdjacentHTML('beforeend', `
      <div class="di-panel" id="di-panel" role="dialog" aria-label="System status">
        <div class="di-p-head">
          <span class="di-p-ic" id="di-p-ic"></span>
          <div class="di-p-title"><b id="di-p-state">All clear</b><span id="di-p-desc">No activity detected</span></div>
          <span class="di-p-clock di-num" id="di-p-clock"></span>
        </div>
        <div class="di-p-stats">
          <div><span>Uptime</span><b class="di-num" id="di-p-up">—</b></div>
          <div><span>Last alert</span><b id="di-p-last">—</b></div>
          <div><span>Network</span><b id="di-p-net">—</b></div>
        </div>
        <div class="di-p-modes" id="di-p-modes">${MODES.map(m =>
          `<button type="button" class="di-chip" data-mode="${m.key}" aria-pressed="false">${icon(m.icon)}<span>${m.label}</span></button>`).join('')}</div>
        <div class="di-p-acts">
          <button type="button" class="di-act di-act-main" id="di-p-talk">${icon('mic')}<span>Talk to Narada</span></button>
          <button type="button" class="di-act" id="di-p-cam" aria-label="Open the camera">${icon('camera')}</button>
          <button type="button" class="di-act" id="di-p-close" aria-label="Close">${icon('minimise')}</button>
        </div>
      </div>`);
    panel = $('di-panel');
    hud.setAttribute('role', 'button');
    hud.setAttribute('tabindex', '0');
    hud.setAttribute('aria-expanded', 'false');
    hud.setAttribute('aria-label', 'System status');

    hud.addEventListener('click', e => { if (!open) { e.stopPropagation(); setOpen(true); } });
    hud.addEventListener('keydown', e => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target === hud) { e.preventDefault(); setOpen(!open); }
    });
    document.addEventListener('click', e => { if (open && !hud.contains(e.target)) setOpen(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && open) setOpen(false); });
    panel.addEventListener('click', onPanelClick);
    return true;
  }

  function onPanelClick(e) {
    e.stopPropagation();
    const chip = e.target.closest('.di-chip');
    if (chip && window.G) {
      const on = chip.getAttribute('aria-pressed') === 'true';
      chip.setAttribute('aria-pressed', String(!on));      // answers the finger at once; the server confirms
      G.toggleMode(chip.dataset.mode, on);
      return;
    }
    const act = e.target.closest('.di-act');
    if (!act) return;
    if (act.id === 'di-p-talk') {
      setOpen(false);
      if (window.G) G.nav('narada');
      if (window.N) N.toggleVoice();
    } else if (act.id === 'di-p-cam') {
      setOpen(false);
      if (window.G) G.nav('dashboard');
    } else {
      setOpen(false);
    }
  }

  // ── open / close ───────────────────────────────────────────
  function setOpen(v) {
    if (!hud || open === v) return;
    open = v;
    if (open) {
      render();
      hud.style.setProperty('--di-h', panel.offsetHeight + 'px');
      tickClock();
      clockTimer = setInterval(tickClock, 1000);
    } else {
      hud.style.removeProperty('--di-h');
      clearInterval(clockTimer);
    }
    hud.classList.toggle('di-open', open);
    hud.setAttribute('aria-expanded', String(open));
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

  function tickClock() {
    const c = $('di-p-clock');
    if (c) c.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (last && last.uptime_seconds != null) {
      const up = $('di-p-up');
      if (up) up.textContent = fmtUptime(last.uptime_seconds + (Date.now() - last._at) / 1000);
    }
  }

  function render() {
    if (!panel || !last) return;
    const s = last, talking = !!(window.N && N.voiceActive());
    const state = s.alert_active ? ['stop', 'Alert', s.danger_info || 'Threat detected', 'alert']
      : s.night_presence_alert ? ['night-mode', 'Night presence', 'Someone is moving at night', 'warn']
      : ['all-clear', 'All clear', 'No activity detected', ''];
    $('di-p-ic').innerHTML = icon(state[0]);
    $('di-p-state').textContent = state[1];
    $('di-p-desc').textContent = state[2];
    panel.dataset.tone = state[3];
    $('di-p-last').textContent = s.last_alert ? ago(s.last_alert) : 'Never';
    $('di-p-net').textContent = s.net_online === false ? 'Offline' : 'Online';
    const modes = s.modes || {};
    panel.querySelectorAll('.di-chip').forEach(c => c.setAttribute('aria-pressed', String(!!modes[c.dataset.mode])));
    $('di-p-talk').lastElementChild.textContent = talking ? 'End conversation' : 'Talk to Narada';
    tickClock();
  }

  // ── notices: pass through the pill and leave ───────────────
  function notify(iconName, text, ms = 2600) {
    if (!hud) return;
    $('di-notice-ic').innerHTML = iconName ? icon(iconName) : '';
    $('di-notice-lbl').textContent = text;
    // Wide enough for the words, measured rather than guessed.
    hud.style.setProperty('--di-notice-w', Math.min(320, 62 + text.length * 7.1) + 'px');
    hud.classList.add('di-notice');
    hud.classList.remove('di-bump'); void hud.offsetWidth; hud.classList.add('di-bump');
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => hud.classList.remove('di-notice'), ms);
  }

  // ── state from the server ──────────────────────────────────
  function onState(s) {
    if (!hud && !build()) return;
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
        hud.classList.remove('di-shake'); void hud.offsetWidth; hud.classList.add('di-shake');
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

    if (open) render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();

  return { onState, notify, open: () => setOpen(true), close: () => setOpen(false) };
})();
window.DI = DI;

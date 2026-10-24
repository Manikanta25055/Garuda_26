// ════════════════════════════════════════════════════════════════════
// Detection rules: what each thing the camera sees does in Home, Night and
// Away, and quiet or alert zones drawn on the camera picture.
// Talks to /api/detection/* (garuda_routes/zones.py). Needs only
// G._apiFn, G._base and G._authQuery from app.js.
// ════════════════════════════════════════════════════════════════════
'use strict';

const Z = (() => {
  const $ = id => document.getElementById(id);
  const api = (m, u, b) => G._apiFn(m, u, b);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const handler = (type, fn, ...args) =>
    `data-${type}="${fn}"` + (args.length ? ` data-args="${esc(JSON.stringify(args))}"` : '');
  const VALUE = { $: 'value' };
  const STATES = ['home', 'night', 'away'];
  const ACTION_WORD = { alert: 'Alert', log: 'Log', ignore: 'Ignore' };
  const errText = e => (e && (e.detail || e.message)) || 'Something went wrong';

  let _danger = new Set();
  let _labels = [];
  let _rules = {};
  let _zones = [];
  let _drag = null;

  function msg(id, text, ok) {
    const el = $(id); if (!el) return;
    el.textContent = text;
    el.className = 'msg ' + (ok ? 'ok' : 'err');
    el.classList.remove('hidden');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.add('hidden'), 5000);
  }

  const fallback = label => _danger.has(label) ? 'alert' : 'log';
  const actionFor = (label, state) => (_rules[label] && _rules[label][state]) || fallback(label);

  function _take(d) {
    _danger = new Set((d.danger_labels || []).map(l => l.toLowerCase()));
    _rules = JSON.parse(JSON.stringify(d.rules || {}));
    _zones = JSON.parse(JSON.stringify(d.zones || []));
    const seen = new Set();
    _labels = [...(d.danger_labels || []), 'person', ...(d.watch_labels || []), ...Object.keys(_rules)]
      .map(l => l.toLowerCase()).filter(l => l && !seen.has(l) && seen.add(l));
  }

  async function load() {
    try { _take(await api('GET', '/api/detection/rules')); }
    catch (e) { msg('zr-rules-msg', errText(e), false); return; }
    renderRules();
    renderZones();
    refreshPicture();
  }

  // ── Rules ────────────────────────────────────────────────
  function renderRules() {
    const box = $('zr-rules');
    if (!box) return;
    const head = `<div class="zr-row zr-head"><span>Thing</span>${STATES.map(s =>
      `<span>${s[0].toUpperCase() + s.slice(1)}</span>`).join('')}</div>`;
    box.innerHTML = head + _labels.map(label => `<div class="zr-row">
        <span class="zr-label">${esc(label)}${_danger.has(label) ? ' <span class="zr-tag">danger</span>' : ''}</span>
        ${STATES.map(state => `<select class="input input-sm zr-act zr-${esc(actionFor(label, state))}"
            aria-label="${esc(label)} in ${state}" ${handler('change', 'Z.setRule', label, state, VALUE)}>
          ${Object.keys(ACTION_WORD).map(a => `<option value="${a}"${a === actionFor(label, state) ? ' selected' : ''}>${ACTION_WORD[a]}</option>`).join('')}
        </select>`).join('')}
      </div>`).join('');
  }

  function setRule(label, state, action) {
    const rule = { ...(_rules[label] || {}) };
    if (action === fallback(label)) delete rule[state]; else rule[state] = action;
    if (Object.keys(rule).length) _rules[label] = rule; else delete _rules[label];
    renderRules();
  }

  function addLabel() {
    const input = $('zr-new-label');
    const label = (input.value || '').trim().toLowerCase();
    if (!label) return;
    if (!_labels.includes(label)) _labels.push(label);
    input.value = '';
    renderRules();
  }

  async function saveRules() {
    try {
      _take(await api('POST', '/api/detection/rules', { rules: _rules }));
      renderRules();
      msg('zr-rules-msg', 'Saved. The camera follows the new rules now.', true);
    } catch (e) { msg('zr-rules-msg', errText(e), false); }
  }

  // ── Zones ────────────────────────────────────────────────
  function refreshPicture() {
    const img = $('zr-picture');
    if (!img) return;
    const auth = G._authQuery ? G._authQuery() : '';
    img.onload = () => { img.classList.remove('zr-missing'); $('zr-empty').classList.add('hidden'); };
    img.onerror = () => { img.classList.add('zr-missing'); $('zr-empty').classList.remove('hidden'); };
    img.src = `${G._base ? G._base() : ''}/api/snapshot?${auth ? auth + '&' : ''}t=${Date.now()}`;
  }

  const pct = v => `${(v * 100).toFixed(2)}%`;
  const place = (el, r) => Object.assign(el.style, {
    left: pct(r[0]), top: pct(r[1]), width: pct(r[2] - r[0]), height: pct(r[3] - r[1]),
  });

  function renderZones() {
    renderBoxes();
    renderList();
  }

  function renderBoxes() {
    const draw = $('zr-draw');
    if (!draw) return;
    draw.querySelectorAll('.zr-box:not(.zr-ghost)').forEach(b => b.remove());
    _zones.forEach((z, i) => {
      const box = document.createElement('div');
      box.className = `zr-box zr-${z.mode}`;
      box.textContent = z.name;
      place(box, z.rect);
      draw.appendChild(box);
      box.dataset.i = i;
    });
  }

  function renderList() {
    const list = $('zr-zones');
    if (!list) return;
    list.innerHTML = _zones.length ? _zones.map((z, i) => `<div class="zr-zone" data-i="${i}">
        <span class="zr-swatch zr-${esc(z.mode)}"></span>
        <input class="input input-sm" value="${esc(z.name)}" maxlength="40" aria-label="Zone name" ${handler('change', 'Z.setZone', i, 'name', VALUE)}/>
        <select class="input input-sm" aria-label="Zone kind" ${handler('change', 'Z.setZone', i, 'mode', VALUE)}>
          <option value="quiet"${z.mode === 'quiet' ? ' selected' : ''}>Quiet</option>
          <option value="alert"${z.mode === 'alert' ? ' selected' : ''}>Alert</option>
        </select>
        <input class="input input-sm" value="${esc((z.labels || []).join(', '))}" placeholder="Everything" aria-label="Things this zone covers"
          ${handler('change', 'Z.setZone', i, 'labels', VALUE)}/>
        <button class="btn btn-ghost btn-sm" aria-label="Delete zone" ${handler('click', 'Z.deleteZone', i)}>Delete</button>
      </div>`).join('') : '<div class="zr-none">No zones yet: the rules above apply everywhere.</div>';
  }

  function setZone(i, field, value) {
    const z = _zones[i];
    if (!z) return;
    if (field === 'labels') z.labels = String(value).split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    else z[field] = value;
    // Only the picture and the swatch change: rebuilding the list here would
    // pull the field out from under the blur that sent this change.
    renderBoxes();
    const sw = document.querySelector(`#zr-zones .zr-zone[data-i="${i}"] .zr-swatch`);
    if (sw) sw.className = `zr-swatch zr-${z.mode}`;
  }

  function deleteZone(i) {
    _zones.splice(i, 1);
    renderZones();
  }

  async function saveZones() {
    try {
      _take(await api('POST', '/api/detection/zones', { zones: _zones }));
      renderZones();
      msg('zr-zones-msg', 'Zones saved.', true);
    } catch (e) { msg('zr-zones-msg', errText(e), false); }
  }

  // Drawing: a drag on the picture makes a rectangle, kept in 0..1 of its size.
  function _point(ev, area) {
    const r = area.getBoundingClientRect();
    const clamp = v => Math.min(1, Math.max(0, v));
    return [clamp((ev.clientX - r.left) / r.width), clamp((ev.clientY - r.top) / r.height)];
  }
  function _rect(a, b) {
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  }
  function _wireDrawing() {
    const area = $('zr-draw');
    if (!area || area._wired) return;
    area._wired = true;
    area.addEventListener('pointerdown', ev => {
      const start = _point(ev, area);
      const ghost = document.createElement('div');
      ghost.className = 'zr-box zr-quiet zr-ghost';
      area.appendChild(ghost);
      _drag = { start, ghost };
      area.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    area.addEventListener('pointermove', ev => {
      if (_drag) place(_drag.ghost, _rect(_drag.start, _point(ev, area)));
    });
    const finish = ev => {
      if (!_drag) return;
      const rect = _rect(_drag.start, _point(ev, area));
      _drag.ghost.remove();
      _drag = null;
      if (rect[2] - rect[0] < 0.03 || rect[3] - rect[1] < 0.03) return;   // a tap, not a zone
      _zones.push({ name: `Zone ${_zones.length + 1}`, rect, mode: 'quiet', labels: [] });
      renderZones();
    };
    area.addEventListener('pointerup', finish);
    area.addEventListener('pointercancel', () => { if (_drag) { _drag.ghost.remove(); _drag = null; } });
  }

  function onNav(page) {
    if (page !== 'a-zones') return;
    _wireDrawing();
    load();
  }

  return { onNav, setRule, addLabel, saveRules, setZone, deleteZone, saveZones, refreshPicture };
})();
window.Z = Z;

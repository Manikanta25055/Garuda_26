// ════════════════════════════════════════════════════════════════════
// Garuda Home — devices, scenes, automations, schedules and insights.
// The Drishti feature set, merged into the Garuda app. Talks to /api/home/*.
// Separate from app.js on purpose: it only needs G.getSession(), G._apiFn()
// and a few hooks (onLogin, onNav, onState, decorateChatReply).
// ════════════════════════════════════════════════════════════════════
'use strict';

const H = (() => {
  const $ = id => document.getElementById(id);
  const api = (m, u, b) => G._apiFn(m, u, b);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // A handler in markup: actions.js calls `fn` with these arguments when the
  // event happens. Nothing here is script; values travel as JSON in data-args.
  // (They were written into onclick="H.x('...')", where escaping could not
  // keep them from becoming code, and inline handlers meant the page had to
  // allow inline script at all.) THIS is the element, VALUE its value,
  // DATA(k) its data-k attribute, Q(sel) document.querySelector(sel).
  const THIS = { $: 'this' }, VALUE = { $: 'value' };
  const DATA = key => ({ $: 'data', key }), Q = sel => ({ $q: sel });
  const handler = (type, fn, ...args) =>
    `data-${type}="${fn}"` + (args.length ? ` data-args="${esc(JSON.stringify(args))}"` : '');
  const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  let _role = 'user';
  let _page = '';
  let _overview = null;
  let _types = null;
  let _seenNotices = new Set();
  let _sceneDraft = null;
  let _settings = {};
  const _busy = new Set();          // devices mid-toggle: pushes sent before the server answers are ignored

  const isAdmin = () => _role === 'admin';
  const errText = e => (e && (e.detail || e.message)) || 'Something went wrong';

  function msg(id, text, ok) {
    const el = $(id); if (!el) return;
    el.textContent = text;
    el.className = 'msg ' + (ok ? 'ok' : 'err');
    el.classList.remove('hidden');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.add('hidden'), 5000);
  }

  function ago(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return new Date(ts * 1000).toLocaleDateString();
  }
  const clock = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const whenLabel = ts => {
    const d = new Date(ts * 1000), now = new Date();
    const day = d.toDateString() === now.toDateString() ? 'today'
      : d.toLocaleDateString([], { weekday: 'short' });
    return `${day} ${clock(ts)}`;
  };

  // ── lifecycle hooks ───────────────────────────────────────
  function onLogin(session) {
    _role = session.role;
    document.body.classList.toggle('ha-is-admin', isAdmin());
    _seenNotices = new Set();
  }

  function onNav(pageId) {
    _page = pageId;
    if (pageId === 'devices') loadOverview();
    if (pageId === 'auto') loadAutomations();
    if (pageId === 'insights') loadInsights();
    if (pageId === 'events') loadCaptures();
  }

  // Called with state.home on every websocket push.
  function onState(home) {
    // Garuda (security-only) shows none of the home automation.
    if (!home || home.error || G.product === 'security') return;
    (home.notices || []).forEach(n => {
      if (_seenNotices.has(n.id)) return;
      _seenNotices.add(n.id);
      // Only notices raised after this page loaded pop up; older ones wait on
      // the Devices page.
      if (Date.now() / 1000 - n.ts < 60) G.showToast(n.text, 'warning', 8000);
    });
    // Device states arrive with every push; re-render only when one changed.
    if (_overview && home.states) {
      let changed = false;
      _overview.devices.forEach(d => {
        const s = home.states[d.id];
        if (s && !_busy.has(d.id) && (s[0] !== d.state || s[1] !== d.available)) { d.state = s[0]; d.available = s[1]; changed = true; }
      });
      const known = new Set(_overview.devices.map(d => d.id));
      const noticesChanged = (home.notices || []).length !== (_overview.notices || []).length;
      if (Object.keys(home.states).some(id => !known.has(id)) || noticesChanged) { autoLoadOverview(); }
      else if (changed && _page === 'devices') renderDevicesPage();
    }
    renderGlance(home);
    const badge = document.querySelector('.ios-item[data-page=auto] .ios-label');
    if (badge) badge.textContent = home.pending_proposals ? `Automate (${home.pending_proposals})` : 'Automate';
  }

  // ── dashboard glance (non-admin) ──────────────────────────
  let _lastHome = null;
  function renderGlance(home) {
    _lastHome = home;
    const el = $('ha-glance-body'); if (!el) return;
    const devices = (_overview && _overview.devices) || [];
    const on = devices.filter(d => d.actuator && d.state === 'on');
    const scenes = (_overview && _overview.scenes) || [];
    el.innerHTML = `
      <div class="ha-glance-stats">
        <div><span class="ha-big">${home.devices_on}</span><span class="ha-sub">of ${home.devices} on</span></div>
        <div><span class="ha-big">${home.owner_presence === 'away' ? 'Away' : 'Home'}</span><span class="ha-sub">presence</span></div>
        <div><span class="ha-big">${home.pending_proposals || 0}</span><span class="ha-sub">to confirm</span></div>
      </div>
      <div class="ha-glance-on">${on.length ? on.map(d => `<span class="ha-chip on">${esc(d.name)}</span>`).join('') : '<span class="ha-sub">Everything is off.</span>'}</div>
      <div class="ha-glance-actions">
        ${scenes.slice(0, 3).map(s => `<button class="btn btn-ghost btn-sm" ${handler('click', 'H.runScene', s.id)}>${esc(s.name)}</button>`).join('')}
        <button class="btn btn-ghost btn-sm ha-danger-btn" ${handler('click', 'H.allOff')}>All off</button>
      </div>`;
    if (!_overview) autoLoadOverview();
  }

  // ── Devices page ──────────────────────────────────────────
  let _overviewAt = 0, _overviewBusy = false;
  async function loadOverview() {
    if (_overviewBusy) return;
    _overviewBusy = true; _overviewAt = Date.now();
    try {
      _overview = await api('GET', '/api/home/overview');
    } catch (e) {
      if (_page === 'devices') G.showToast(errText(e), 'error');
      return;
    } finally { _overviewBusy = false; }
    if (_page === 'devices') renderDevicesPage();
    if (_lastHome) renderGlance(_lastHome);
  }
  // Refreshes triggered by websocket pushes, at most every 10 s: the API
  // allows 30 requests a minute per IP.
  const autoLoadOverview = () => { if (Date.now() - _overviewAt > 10000) loadOverview(); };

  function renderDevicesPage() {
    const o = _overview; if (!o) return;
    const ctx = o.context || {};
    $('ha-presence').innerHTML = `
      <span class="ha-pill ${ctx.owner_presence === 'away' ? 'warn' : 'ok'}">${ctx.owner_presence === 'away' ? 'Away' : 'Home'}</span>
      <span class="ha-pill ${o.occupancy === 'occupied' ? 'ok' : ''}">${o.occupancy === 'occupied' ? `${o.person_count || 1} in view` : 'Room empty'}</span>
      ${ctx.security && ctx.security !== 'clear' ? `<span class="ha-pill bad">${esc(ctx.security.replace('_', ' '))}</span>` : ''}
      <span class="ha-pill ${o.ai && o.ai.nim && o.ai.nim.configured ? 'ok' : 'warn'}">${o.ai && o.ai.nim && o.ai.nim.configured ? 'AI ready' : 'AI offline'}</span>`;

    // Notices
    $('ha-notices').innerHTML = (o.notices || []).map(n => `
      <div class="ha-notice ${esc(n.kind)}">
        <div class="ha-notice-text">${esc(n.text)}<span class="ha-sub"> · ${ago(n.ts)}</span></div>
        <div class="ha-notice-actions">
          ${(n.actions || []).map((a, i) => `<button class="btn btn-primary btn-sm" ${handler('click', 'H.noticeAction', n.id, i)}>${esc(a.label)}</button>`).join('')}
          <button class="btn btn-ghost btn-sm" ${handler('click', 'H.dismissNotice', n.id)}>Dismiss</button>
        </div>
      </div>`).join('');

    // Scenes
    $('ha-scenes').innerHTML = (o.scenes || []).length
      ? o.scenes.map(s => `
        <div class="ha-scene">
          <button class="ha-scene-run" ${handler('click', 'H.runScene', s.id)}>
            <span class="ha-scene-name">${esc(s.name)}</span>
            <span class="ha-sub">${s.actions.map(a => `${esc(deviceName(a.device))} ${esc(a.action)}`).join(' · ')}</span>
          </button>
          ${isAdmin() ? `<button class="ha-x" title="Delete scene" aria-label="Delete scene ${esc(s.name)}" ${handler('click', 'H.deleteScene', s.id)}>&times;</button>` : ''}
        </div>`).join('')
      : `<div class="ha-empty">${isAdmin() ? 'No scenes yet. Group devices into one tap — “Leave home”, “Study”.' : 'No scenes yet.'}</div>`;

    // Devices, grouped by room
    const rooms = {};
    (o.devices || []).forEach(d => { (rooms[d.room || 'Other'] = rooms[d.room || 'Other'] || []).push(d); });
    $('ha-rooms').innerHTML = Object.keys(rooms).length ? Object.entries(rooms).map(([room, list]) => `
      <div class="ha-room">
        <div class="ha-room-name">${esc(room)}</div>
        <div class="ha-tiles">${list.map(tile).join('')}</div>
      </div>`).join('')
      : '<div class="ha-empty">No devices yet. An admin can add one below.</div>';

    if (isAdmin()) renderManage();
  }

  function deviceName(id) {
    const d = ((_overview && _overview.devices) || []).find(x => x.id === id);
    return d ? d.name : id;
  }

  function tile(d) {
    if (!d.actuator) {
      return `<div class="ha-tile sensor"><div class="ha-tile-name">${esc(d.name)}</div>
        <div class="ha-tile-state">${esc(d.state ?? '—')}</div><div class="ha-sub">${esc(d.type.replace('sensor.', ''))}</div></div>`;
    }
    const on = d.state === 'on';
    const off = d.enabled === false;
    return `<button class="ha-tile ${on ? 'on' : ''} ${!d.available || off ? 'unavail' : ''}"
        ${!d.available || off ? 'disabled' : ''} aria-pressed="${on}"
        ${handler('click', 'H.toggleDevice', d.id, THIS)}>
      <div class="ha-tile-top"><span class="ha-dot"></span><span class="ha-sub">${esc(d.type)}</span></div>
      <div class="ha-tile-name">${esc(d.name)}</div>
      <div class="ha-tile-state">${off ? 'Disabled' : !d.available ? 'Unreachable' : on ? 'On' : 'Off'}</div>
    </button>`;
  }

  // The tile flips under the finger; the server is told afterwards, and the
  // tile only goes back if it says no.
  function paintTile(btn, on) {
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', String(on));
    const state = btn.querySelector('.ha-tile-state');
    if (state) state.textContent = on ? 'On' : 'Off';
  }
  async function toggleDevice(id, btn) {
    if (btn.dataset.pending === '1') return;
    const on = btn.getAttribute('aria-pressed') !== 'true';
    const dev = ((_overview && _overview.devices) || []).find(d => d.id === id);
    btn.dataset.pending = '1';
    _busy.add(id);
    paintTile(btn, on);
    if (dev) dev.state = on ? 'on' : 'off';
    if (navigator.vibrate) { try { navigator.vibrate(8); } catch (_) {} }
    try {
      await api('POST', `/api/home/devices/${encodeURIComponent(id)}/set`, { action: on ? 'on' : 'off' });
    } catch (e) {
      if (dev) dev.state = on ? 'off' : 'on';
      if (btn.isConnected) paintTile(btn, !on);
      G.showToast(errText(e), 'error');
    }
    delete btn.dataset.pending;
    _busy.delete(id);
  }

  async function allOff() {
    if (!await G.confirmAction({ title: 'Turn everything off?', body: 'Every device in the house is switched off.', confirmLabel: 'All off', sound: 'off' })) return;
    try {
      const r = await api('POST', '/api/home/all-off', {});
      G.showToast(r.turned_off.length ? `Turned off ${r.turned_off.join(', ')}` : 'Everything was already off', r.failed.length ? 'warning' : 'success');
    } catch (e) { G.showToast(errText(e), 'error'); }
    loadOverview();
  }

  async function runScene(id) {
    try {
      const r = await api('POST', `/api/home/scenes/${encodeURIComponent(id)}/run`);
      G.showToast(r.ok ? 'Scene done' : `Scene ran with problems: ${r.reason}`, r.ok ? 'success' : 'warning');
    } catch (e) { G.showToast(errText(e), 'error'); }
    loadOverview();
  }

  async function deleteScene(id) {
    if (!await G.confirmAction({ title: 'Delete this scene?', confirmLabel: 'Delete' })) return;
    try { await api('DELETE', `/api/home/scenes/${encodeURIComponent(id)}`); } catch (e) { G.showToast(errText(e), 'error'); }
    loadOverview();
  }

  async function noticeAction(id, i) {
    const n = ((_overview && _overview.notices) || []).find(x => x.id === id);
    const a = n && n.actions[i]; if (!a) return;
    try {
      if (a.call === 'all_off') await api('POST', '/api/home/all-off', {});
      if (a.call === 'device_off') await api('POST', `/api/home/devices/${encodeURIComponent(a.device)}/set`, { action: 'off' });
      await api('POST', `/api/home/notices/${encodeURIComponent(id)}/dismiss`);
      G.showToast('Done', 'success');
    } catch (e) { G.showToast(errText(e), 'error'); }
    loadOverview();
  }

  async function dismissNotice(id) {
    try { await api('POST', `/api/home/notices/${encodeURIComponent(id)}/dismiss`); } catch (_) {}
    loadOverview();
  }

  // ── composer: one sentence to the home agent ──────────────
  async function instruct() {
    const input = $('ha-cmd'), btn = $('ha-cmd-btn'), out = $('ha-cmd-result');
    const text = input.value.trim(); if (!text) return;
    btn.disabled = true; btn.textContent = '…';
    out.className = 'ha-result'; out.innerHTML = '<span class="ha-sub">Working…</span>';
    try {
      const r = await api('POST', '/api/home/instruct', { text });
      input.value = '';
      out.innerHTML = resultHtml(r);
    } catch (e) {
      out.innerHTML = `<div class="ha-result-text error">${esc(errText(e))}</div>`;
    } finally {
      btn.disabled = false; btn.textContent = 'Send';
      loadOverview();
    }
  }

  function laneLabel(r) {
    if (r.lane === 'agent') return `NVIDIA NIM${r.model ? ' · ' + r.model : ''}`;
    if (r.lane === 'unavailable') return 'NVIDIA NIM unavailable · nothing changed';
    return r.lane || '';
  }

  function resultHtml(r) {
    return `
      <div class="ha-result-text">${esc(r.reply || r.response || '')}</div>
      ${(r.actions || []).filter(a => a !== r.reply).length ? `<div class="ha-chips">${r.actions.filter(a => a !== r.reply).map(a => `<span class="ha-chip">${esc(a)}</span>`).join('')}</div>` : ''}
      ${r.proposal ? proposalCard(r.proposal, true) : ''}
      <div class="ha-sub ha-lane">${esc(laneLabel(r))}</div>`;
  }

  function proposalCard(p, inline) {
    const id = p.proposal_id || p.id;
    return `<div class="ha-proposal">
      <div class="ha-proposal-said">“${esc(p.rule.source_utterance)}”</div>
      <div class="ha-rule-line"><b>When</b> ${esc(p.rendered.when)}</div>
      <div class="ha-rule-line"><b>Then</b> ${esc(p.rendered.then)}</div>
      ${p.conflict ? `<div class="ha-sub warn">Overlaps an existing rule: ${esc(p.conflict.source_utterance || p.conflict.id || '')}</div>` : ''}
      <div class="ha-proposal-actions">
        ${isAdmin() ? `<button class="btn btn-primary btn-sm" ${handler('click', 'H.confirmProposal', id)}>Confirm</button>`
                    : '<span class="ha-sub">Waiting for an admin to confirm.</span>'}
        <button class="btn btn-ghost btn-sm" ${handler('click', 'H.discardProposal', id)}>Discard</button>
        ${inline ? `<button class="btn btn-ghost btn-sm" ${handler('click', 'G.nav', 'auto', Q('.ios-item[data-page=auto]'))}>Open Automations</button>` : ''}
      </div>
    </div>`;
  }

  // Chat replies from the agent carry what it did; show it under the text.
  function decorateChatReply(bodyEl, res) {
    if (!bodyEl || !res) return;
    const extra = [];
    const acts = (res.actions || []).filter(a => a !== (res.response || res.reply));
    if (acts.length) extra.push(`<div class="ha-chips">${acts.map(a => `<span class="ha-chip">${esc(a)}</span>`).join('')}</div>`);
    if (res.proposal) extra.push(proposalCard(res.proposal, true));
    if (res.lane && res.lane !== 'custom') extra.push(`<div class="ha-sub ha-lane">${esc(laneLabel(res))}</div>`);
    if (!extra.length) return;
    const box = document.createElement('div');
    box.className = 'ha-chat-extra';
    box.innerHTML = extra.join('');
    bodyEl.parentElement.appendChild(box);
  }

  // ── scene editor (admin) ──────────────────────────────────
  function openSceneEditor() {
    const el = $('ha-scene-editor');
    const devices = ((_overview && _overview.devices) || []).filter(d => d.actuator);
    _sceneDraft = {};
    el.innerHTML = `
      <div class="settings-section-title">New scene</div>
      <input id="ha-scene-name" class="input" maxlength="48" placeholder="Scene name (e.g. Leave home)"/>
      <div class="ha-scene-steps">${devices.map(d => `
        <div class="ha-step">
          <span>${esc(d.name)}<span class="ha-sub"> · ${esc(d.room)}</span></span>
          <div class="ha-seg" data-dev="${esc(d.id)}">
            <button data-v="" class="sel" ${handler('click', 'H.pickStep', THIS)}>—</button>
            <button data-v="on" ${handler('click', 'H.pickStep', THIS)}>On</button>
            <button data-v="off" ${handler('click', 'H.pickStep', THIS)}>Off</button>
          </div>
        </div>`).join('') || '<div class="ha-empty">Add a device first.</div>'}</div>
      <div class="form-row">
        <button class="btn btn-ghost btn-sm" ${handler('click', 'UI.addClass', 'ha-scene-editor', 'hidden')}>Cancel</button>
        <button class="btn btn-primary btn-sm" ${handler('click', 'H.saveScene')}>Save scene</button>
      </div>
      <div id="ha-scene-msg" class="msg hidden"></div>`;
    el.classList.remove('hidden');
    $('ha-scene-name').focus();
  }

  function pickStep(btn) {
    const seg = btn.parentElement;
    seg.querySelectorAll('button').forEach(b => b.classList.toggle('sel', b === btn));
    if (btn.dataset.v) _sceneDraft[seg.dataset.dev] = btn.dataset.v; else delete _sceneDraft[seg.dataset.dev];
  }

  async function saveScene() {
    const name = $('ha-scene-name').value.trim();
    const actions = Object.entries(_sceneDraft || {}).map(([device, action]) => ({ device, action }));
    if (!name || !actions.length) return msg('ha-scene-msg', 'Give it a name and pick at least one device.', false);
    try {
      await api('POST', '/api/home/scenes', { name, actions });
      $('ha-scene-editor').classList.add('hidden');
      loadOverview();
    } catch (e) { msg('ha-scene-msg', errText(e), false); }
  }

  // ── device management (admin) ─────────────────────────────
  async function ensureTypes() {
    if (_types) return _types;
    _types = await api('GET', '/api/home/device-types');
    const sel = $('ha-add-type');
    sel.innerHTML = Object.keys(_types.types).map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
    return _types;
  }

  // What the service sees of the relay board and the Wi-Fi devices, so wiring
  // can be checked from the page instead of a terminal.
  async function renderHardware() {
    let hw;
    try { hw = (await api('GET', '/api/system/info')).hardware; } catch (_) { return; }
    if (!hw) return;
    const mqtt = hw.mqtt || { devices: [] };
    $('ha-hardware-summary').textContent =
      (hw.gpio ? 'Relay board: ' + hw.polarity : 'No GPIO on this machine: relays are simulated') +
      ' · MQTT broker ' + mqtt.broker + (mqtt.connected ? ' connected' : ' not connected');
    const relays = (hw.channels || []).map(c => `
      <div class="ha-manage-row">
        <div class="ha-manage-main"><b>Channel ${c.channel}</b>
          <span class="ha-sub">GPIO ${c.pin} · ${c.device ? esc(c.name) + ' · ' + esc(c.state) : 'free'}${c.device && !c.driven ? ' · pin not driven yet' : ''}</span></div>
      </div>`);
    const wifi = mqtt.devices.map(d => `
      <div class="ha-manage-row">
        <div class="ha-manage-main"><b>${esc(d.name)}</b>
          <span class="ha-sub">${esc(d.topic)} · ${d.seen ? 'reporting' : 'nothing heard yet'}</span></div>
      </div>`);
    $('ha-hardware').innerHTML = relays.concat(wifi).join('');
  }

  async function renderManage() {
    if (isAdmin()) renderHardware();
    try { await ensureTypes(); } catch (_) { return; }
    const used = new Set((_overview.devices || []).filter(d => d.transport.kind === 'relay').map(d => d.transport.channel));
    const free = _types.channels.filter(c => !used.has(c));
    const chSel = $('ha-add-channel');
    const keep = chSel.value;
    chSel.innerHTML = free.map(c => `<option value="${c}">Channel ${c}</option>`).join('') || '<option value="">No free channel</option>';
    if (free.includes(Number(keep))) chSel.value = keep;
    $('ha-manage-list').innerHTML = (_overview.devices || []).map(d => `
      <div class="ha-manage-row">
        <div class="ha-manage-main"><b>${esc(d.name)}</b>
          <span class="ha-sub">${esc(d.room)} · ${esc(d.type)} · ${d.transport.kind === 'relay' ? 'relay ' + d.transport.channel : 'mqtt ' + esc(d.transport.topic_base)}</span></div>
        <input class="input input-sm ha-watts" type="number" min="0" max="5000" value="${d.watts ?? ''}" placeholder="W"
               aria-label="Watts for ${esc(d.name)}" ${handler('change', 'H.setWatts', d.id, VALUE)}/>
        <div class="toggle ${d.enabled === false ? '' : 'on'}" title="Enabled" ${handler('click', 'H.setEnabled', d.id, THIS)}></div>
        <button class="ha-x" title="Remove" aria-label="Remove ${esc(d.name)}" data-name="${esc(d.name)}" ${handler('click', 'H.deleteDevice', d.id, DATA('name'))}>&times;</button>
      </div>`).join('') || '<div class="ha-empty">No devices yet.</div>';
  }

  function onKindChange() {
    const mqtt = $('ha-add-kind').value === 'mqtt';
    $('ha-add-channel').classList.toggle('hidden', mqtt);
    $('ha-add-topic').classList.toggle('hidden', !mqtt);
  }

  function idFrom(name) {
    return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
      .replace(/^([^a-z])/, 'd$1').slice(0, 32);
  }

  async function addDevice() {
    const name = $('ha-add-name').value.trim(), room = $('ha-add-room').value.trim();
    const id = idFrom(name);
    if (id.length < 2) return msg('ha-manage-msg', 'Give the device a name of at least two letters.', false);
    if (!room) return msg('ha-manage-msg', 'Which room is it in?', false);
    const kind = $('ha-add-kind').value;
    const body = {
      id, name, room, type: $('ha-add-type').value,
      transport: kind === 'relay' ? { kind, channel: Number($('ha-add-channel').value) }
                                  : { kind, topic_base: $('ha-add-topic').value.trim() },
    };
    const w = $('ha-add-watts').value;
    if (w !== '') body.watts = Number(w);
    try {
      await api('POST', '/api/home/devices', body);
      ['ha-add-name', 'ha-add-room', 'ha-add-topic', 'ha-add-watts'].forEach(i => { $(i).value = ''; });
      msg('ha-manage-msg', `Added ${name}.`, true);
      loadOverview();
    } catch (e) { msg('ha-manage-msg', errText(e), false); }
  }

  async function setWatts(id, value) {
    try {
      await api('PATCH', `/api/home/devices/${encodeURIComponent(id)}`, { watts: value === '' ? null : Number(value) });
    } catch (e) { G.showToast(errText(e), 'error'); }
  }

  // A switch moves at once and is given time to finish before the list is
  // redrawn around it; the redraw then lands on the state it already shows.
  const settle = p => Promise.all([p, new Promise(r => setTimeout(r, 520))]).then(v => v[0]);

  async function setEnabled(id, sw) {
    const enabled = sw.classList.toggle('on');
    try { await settle(api('PATCH', `/api/home/devices/${encodeURIComponent(id)}`, { enabled })); }
    catch (e) { G.showToast(errText(e), 'error'); }
    loadOverview();
  }

  async function deleteDevice(id, name) {
    if (!await G.confirmAction({ title: `Remove ${name}?`, body: 'Rules that use it are kept but paused.', confirmLabel: 'Remove' })) return;
    try {
      const r = await api('DELETE', `/api/home/devices/${encodeURIComponent(id)}`);
      if (r.orphaned) G.showToast(`${r.orphaned} rule(s) paused because they used ${name}`, 'warning');
    } catch (e) { G.showToast(errText(e), 'error'); }
    loadOverview();
  }

  // ── Automations page ──────────────────────────────────────
  async function loadAutomations() {
    let all;
    try {
      all = await api('GET', '/api/home/automations');
    } catch (e) { G.showToast(errText(e), 'error'); return; }
    const rules = all.rules, sched = { schedules: all.schedules }, sugg = { suggestions: all.suggestions };
    _settings = all.settings;
    const pick = { devices: all.devices, scenes: all.scenes };

    $('ha-proposals-wrap').classList.toggle('hidden', !rules.proposals.length);
    $('ha-proposals').innerHTML = rules.proposals.map(p => proposalCard(p, false)).join('');

    $('ha-suggest-wrap').classList.toggle('hidden', !sugg.suggestions.length);
    $('ha-suggestions').innerHTML = sugg.suggestions.map(s => `
      <div class="ha-row">
        <div class="ha-row-main">${esc(s.text)}<div class="ha-sub">Seen on ${s.support_days} days</div></div>
        <div class="ha-row-actions">
          ${isAdmin() ? `<button class="btn btn-primary btn-sm" ${handler('click', 'H.acceptSuggestion', s.id)}>Automate</button>` : ''}
          <button class="btn btn-ghost btn-sm" ${handler('click', 'H.dismissSuggestion', s.id)}>Not now</button>
        </div>
      </div>`).join('');

    $('ha-rules').innerHTML = rules.rules.length ? rules.rules.map(r => `
      <div class="ha-row ${r.enabled === false ? 'off' : ''}">
        <div class="ha-row-main">
          <div>“${esc(r.source_utterance)}”</div>
          <div class="ha-sub"><b>When</b> ${esc(r.rendered.when)} &nbsp; <b>Then</b> ${esc(r.rendered.then)}</div>
          <div class="ha-sub">${r.fired_count ? `Ran ${Number(r.fired_count) || 0}× · last ${ago(r.last_fired)}` : 'Has not run yet'}</div>
        </div>
        ${isAdmin() ? `<div class="ha-row-actions">
          <div class="toggle ${r.enabled === false ? '' : 'on'}" ${handler('click', 'H.toggleRule', r.id, THIS)}></div>
          <button class="ha-x" aria-label="Delete rule" data-name="${esc(r.source_utterance)}" ${handler('click', 'H.deleteRule', r.id, DATA('name'))}>&times;</button></div>` : ''}
      </div>`).join('')
      + (rules.orphaned.length ? `<div class="ha-sub warn">${rules.orphaned.length} rule(s) paused because a device they use was removed.</div>` : '')
      : '<div class="ha-empty">No rules yet. Try: “when nobody is in the room for 10 minutes, turn the lamp off”.</div>';

    const me = (G.getSession() || {}).username;
    $('ha-schedules').innerHTML = sched.schedules.length ? sched.schedules.map(e => `
      <div class="ha-row ${e.enabled === false ? 'off' : ''}">
        <div class="ha-row-main">
          <div>${esc(e.describe)}</div>
          <div class="ha-sub">${e.kind === 'once' ? `Timer · ${whenLabel(e.at)}`
            : `${esc(e.time)} · ${e.days.length === 7 ? 'every day' : e.days.map(d => DAY_NAMES[d]).join(' ')}`}
            ${e.next_run && e.kind !== 'once' ? ` · next ${whenLabel(e.next_run)}` : ''}${e.label ? ' · ' + esc(e.label) : ''}</div>
        </div>
        ${isAdmin() || e.created_by === me ? `<div class="ha-row-actions">
          ${e.kind === 'daily' ? `<div class="toggle ${e.enabled === false ? '' : 'on'}" ${handler('click', 'H.toggleSchedule', e.id, THIS)}></div>` : ''}
          <button class="ha-x" aria-label="Delete schedule" data-name="${esc(e.describe)}" ${handler('click', 'H.deleteSchedule', e.id, DATA('name'))}>&times;</button></div>` : ''}
      </div>`).join('') : '<div class="ha-empty">Nothing scheduled.</div>';

    loadShortcuts();

    // Schedule form
    const devs = pick.devices.filter(d => d.actuator);
    const keepTarget = $('ha-s-target').value;
    $('ha-s-target').innerHTML =
      devs.map(d => `<option value="d:${esc(d.id)}">${esc(d.name)}</option>`).join('') +
      pick.scenes.map(s => `<option value="s:${esc(s.id)}">Scene: ${esc(s.name)}</option>`).join('');
    if (keepTarget) $('ha-s-target').value = keepTarget;
    document.querySelectorAll('.ha-admin-opt').forEach(o => { o.disabled = !isAdmin(); });
    renderDays();

    // Away & vacation
    document.querySelectorAll('#ha-away-card .toggle[data-setting]').forEach(t => {
      t.classList.toggle('on', !!_settings[t.dataset.setting]);
      t.classList.toggle('ha-readonly', !isAdmin());
    });
    $('ha-vac-start').value = _settings.vacation_start || '19:00';
    $('ha-vac-end').value = _settings.vacation_end || '23:30';
    $('ha-lefton').value = _settings.left_on_minutes ?? 120;
    ['ha-vac-start', 'ha-vac-end', 'ha-lefton'].forEach(i => { $(i).disabled = !isAdmin(); });
  }

  // Shortcuts: programs Narada writes from the things it can do. This page
  // lists, runs, pauses and removes them; making one is done by asking Narada.
  async function loadShortcuts() {
    const box = $('ha-shortcuts');
    if (!box) return;
    let data;
    try { data = await api('GET', '/api/home/shortcuts'); } catch (e) { box.innerHTML = `<div class="ha-empty">${esc(errText(e))}</div>`; return; }
    const me = (G.getSession() || {}).username;
    const running = Object.fromEntries((data.running || []).map(r => [r.shortcut, r]));
    box.innerHTML = data.shortcuts.length ? data.shortcuts.map(sc => {
      const mine = isAdmin() || sc.created_by === me, live = running[sc.id], last = (sc.runs || [])[0];
      const steps = sc.rendered.steps.map(line => `<li style="--d:${(line.match(/^ */)[0].length / 2) | 0}">${esc(line.trim())}</li>`).join('');
      return `
      <div class="ha-row ha-shortcut ${sc.enabled === false ? 'off' : ''}">
        <div class="ha-row-main">
          <div><b>${esc(sc.name)}</b></div>
          <div class="ha-sub">${esc(sc.rendered.when)}${sc.rendered.only_if ? ' · only if ' + esc(sc.rendered.only_if) : ''}</div>
          ${(sc.cautions || []).map(w => `<div class="ha-sub warn">${esc(w)}</div>`).join('')}
          <details class="ha-steps"><summary>${sc.rendered.steps.length} step${sc.rendered.steps.length === 1 ? '' : 's'}</summary><ol>${steps}</ol></details>
          <div class="ha-sub">${live ? `Running now: ${esc(String(live.now || '').replace(/_/g, ' '))}`
            : last ? `Last run ${ago(last.started)} · ${esc(last.outcome)}` : 'Has not run yet'} · by ${esc(sc.created_by)}</div>
        </div>
        <div class="ha-row-actions">
          ${live ? `<button class="btn btn-ghost btn-sm" ${handler('click', 'H.cancelShortcut', sc.id)}>Stop</button>`
                 : `<button class="btn btn-primary btn-sm" ${handler('click', 'H.runShortcut', sc.id)}>Run</button>`}
          ${mine && sc.trigger.type !== 'manual' ? `<div class="toggle ${sc.enabled === false ? '' : 'on'}" ${handler('click', 'H.toggleShortcut', sc.id)}></div>` : ''}
          ${mine ? `<button class="ha-x" aria-label="Delete shortcut" data-name="${esc(sc.name)}" ${handler('click', 'H.deleteShortcut', sc.id, DATA('name'))}>&times;</button>` : ''}
        </div>
      </div>`;
    }).join('') : '<div class="ha-empty">No shortcuts yet. Ask Narada: “make a movie night button that turns the lamp off and the TV on”.</div>';
  }
  async function shortcutCall(method, path, done) {
    try {
      await api(method, '/api/home/shortcuts/' + path);
      if (done) G.showToast(done, 'success');
    } catch (e) { G.showToast(errText(e), 'error'); }
    loadShortcuts();
    setTimeout(loadShortcuts, 1500);          // a short run has finished by then
    setTimeout(loadCaptures, 1500);
  }
  const runShortcut = id => shortcutCall('POST', `${encodeURIComponent(id)}/run`, 'Shortcut started');
  const cancelShortcut = id => shortcutCall('POST', `${encodeURIComponent(id)}/cancel`);
  const toggleShortcut = id => shortcutCall('POST', `${encodeURIComponent(id)}/toggle`);
  const deleteShortcut = async (id, name) => (await sureDelete('shortcut', name)) && shortcutCall('DELETE', encodeURIComponent(id), 'Shortcut deleted');

  // Captures: pictures, sound and video kept on the Pi, by a shortcut step,
  // Narada or the buttons here. One run of a shortcut is one card.
  let _capKind = '', _capTimer = null;
  const KIND_WORD = { snapshot: 'Picture', audio: 'Sound', video: 'Video' };
  async function loadCaptures() {
    const box = $('ha-captures');
    if (!box) return;
    clearTimeout(_capTimer);
    let data;
    const q = _capKind ? `?kind=${_capKind}&limit=60` : '?limit=60';
    try { data = await api('GET', '/api/captures' + q); } catch (e) { box.innerHTML = `<div class="ha-empty">${esc(errText(e))}</div>`; return; }
    const me = (G.getSession() || {}).username;
    const base = G._base ? G._base() : '', auth = G._authQuery ? G._authQuery() : '';
    const src = c => `${base}${c.url}${auth ? '?' + auth : ''}`;
    const groups = [], byGroup = {};
    data.captures.forEach(c => {
      const key = c.group || c.id;
      if (!byGroup[key]) { byGroup[key] = []; groups.push(byGroup[key]); }
      byGroup[key].push(c);
    });
    const media = c => {
      if (c.status === 'recording') return `<div class="ha-sub">Recording ${KIND_WORD[c.kind].toLowerCase()}${c.seconds ? ` · ${c.seconds} s` : ''}…</div>`;
      if (c.status !== 'saved') return `<div class="ha-sub warn">${esc(KIND_WORD[c.kind])} failed: ${esc(c.error)}</div>`;
      if (c.kind === 'snapshot') return `<a href="${esc(src(c))}" target="_blank" rel="noopener"><img class="ha-cap-img" loading="lazy" alt="Saved picture" src="${esc(src(c))}"/></a>`;
      if (c.kind === 'audio') return `<audio class="ha-cap-audio" controls preload="none" src="${esc(src(c))}"></audio>`;
      return `<video class="ha-cap-video" controls preload="metadata" playsinline src="${esc(src(c))}"></video>`;
    };
    const item = c => {
      const mine = isAdmin() || c.by === me;
      return `<div class="ha-cap-item">
        ${media(c)}
        <div class="ha-cap-bar"><span class="ha-sub">${esc(KIND_WORD[c.kind])}${c.seconds ? ` · ${c.seconds} s` : ''}</span>
          ${c.status === 'saved' ? `<a class="btn btn-ghost btn-sm" href="${esc(src(c) + (auth ? '&' : '?') + 'download=true')}">Save</a>` : ''}
          ${mine && c.status !== 'recording' ? `<button class="btn btn-ghost btn-sm" title="${c.keep ? 'Kept: never cleared to make room' : 'Keep: never clear it to make room'}" ${handler('click', 'H.keepCapture', c.id, !c.keep)}>${c.keep ? '★ Kept' : '☆ Keep'}</button>
          <button class="ha-x" aria-label="Delete capture" ${handler('click', 'H.deleteCapture', c.id)}>&times;</button>` : ''}
        </div></div>`;
    };
    box.innerHTML = groups.length ? groups.map(g => {
      const first = g[g.length - 1], label = g.find(c => c.label)?.label, alert = g.find(c => c.alert)?.alert;
      return `<div class="ha-row ha-cap-group"><div class="ha-row-main">
        <div><b>${esc(label || 'Saved by hand')}</b> <span class="ha-sub">${esc(whenLabel(first.at))} · ${esc(first.by)}</span></div>
        ${alert ? `<div class="ha-sub warn">During an alert: ${esc(alert)}</div>` : ''}
        <div class="ha-cap-items">${g.slice().reverse().map(item).join('')}</div>
      </div></div>`;
    }).join('') : '<div class="ha-empty">Nothing saved yet. Ask Narada: “when an alert starts, save a picture and the next 10 seconds of sound”.</div>';
    const u = data.usage || {}, meta = $('ha-cap-usage');
    const size = b => b < 1048576 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1048576).toFixed(1)} MB`;
    if (meta) meta.textContent = u.count ? `${u.count} saved · ${size(u.bytes)} of ${size(u.max_bytes)}` : '';
    // A recording is listed at once; look again until it is saved.
    if (data.captures.some(c => c.status === 'recording')) _capTimer = setTimeout(loadCaptures, 2000);
  }
  function filterCaptures(kind) { _capKind = kind || ''; loadCaptures(); }
  async function captureNow(kind) {
    const body = kind === 'snapshot' ? {} : { seconds: 10, wait: false };
    try {
      const r = await api('POST', `/api/captures/${kind}`, body);
      G.showToast(r.result || 'Saved', 'success');
    } catch (e) { G.showToast(errText(e), 'error'); }
    loadCaptures();
  }
  async function keepCapture(id, keep) {
    try { await api('POST', `/api/captures/${encodeURIComponent(id)}/keep`, { keep }); } catch (e) { G.showToast(errText(e), 'error'); }
    loadCaptures();
  }
  async function deleteCapture(id) {
    if (!(await sureDelete('capture', '', 'The file is removed from the Pi and cannot be brought back.'))) return;
    try { await api('DELETE', `/api/captures/${encodeURIComponent(id)}`); } catch (e) { G.showToast(errText(e), 'error'); }
    loadCaptures();
  }

  let _days = [0, 1, 2, 3, 4, 5, 6];
  function renderDays() {
    $('ha-s-days').innerHTML = DAY_NAMES.map((n, i) =>
      `<button class="ha-day ${_days.includes(i) ? 'sel' : ''}" ${handler('click', 'H.toggleDay', i)}>${n}</button>`).join('');
  }
  function toggleDay(i) {
    _days = _days.includes(i) ? _days.filter(d => d !== i) : [..._days, i].sort();
    renderDays();
  }
  function onSchedMode() {
    const daily = $('ha-s-mode').value === 'daily';
    $('ha-s-minutes').classList.toggle('hidden', daily);
    $('ha-s-time').classList.toggle('hidden', !daily);
    $('ha-s-days').classList.toggle('hidden', !daily);
    $('ha-s-action').classList.toggle('hidden', $('ha-s-target').value.startsWith('s:'));
  }

  async function addSchedule() {
    const t = $('ha-s-target').value; if (!t) return;
    const body = t.startsWith('s:') ? { scene: t.slice(2) } : { device: t.slice(2), action: $('ha-s-action').value };
    if ($('ha-s-mode').value === 'daily') {
      body.time = $('ha-s-time').value; body.days = _days;
    } else {
      body.in_minutes = Number($('ha-s-minutes').value);
    }
    try {
      await api('POST', '/api/home/schedules', body);
      msg('ha-s-msg', 'Scheduled.', true);
      loadAutomations();
    } catch (e) { msg('ha-s-msg', errText(e), false); }
  }

  const reloadAuto = p => p.then(loadAutomations).catch(e => { G.showToast(errText(e), 'error'); loadAutomations(); });
  const confirmProposal = id => reloadAuto(api('POST', `/api/home/proposals/${encodeURIComponent(id)}/confirm`)
    .then(() => G.showToast('Rule saved — it is live now', 'success')));
  const discardProposal = id => reloadAuto(api('DELETE', `/api/home/proposals/${encodeURIComponent(id)}`));
  const flip = sw => { if (sw) { sw.classList.toggle('on'); sw.closest('.ha-row')?.classList.toggle('off'); } };
  const toggleRule = (id, sw) => { flip(sw); return reloadAuto(settle(api('POST', `/api/home/rules/${encodeURIComponent(id)}/toggle`))); };
  // Nothing saved on this page is removed without being asked first.
  const sureDelete = (what, name, body) => G.confirmAction({
    title: name ? `Delete ${what} “${name}”?` : `Delete this ${what}?`,
    body: body || 'It stops running and cannot be brought back.', confirmLabel: 'Delete' });
  const deleteRule = async (id, name) => (await sureDelete('automation', name)) && reloadAuto(api('DELETE', `/api/home/rules/${encodeURIComponent(id)}`));
  const toggleSchedule = (id, sw) => { flip(sw); return reloadAuto(settle(api('POST', `/api/home/schedules/${encodeURIComponent(id)}/toggle`))); };
  const deleteSchedule = async (id, name) => (await sureDelete('schedule', name)) && reloadAuto(api('DELETE', `/api/home/schedules/${encodeURIComponent(id)}`));
  const acceptSuggestion = id => reloadAuto(api('POST', `/api/home/suggestions/${encodeURIComponent(id)}/accept`)
    .then(() => G.showToast('Schedule created', 'success')));
  const dismissSuggestion = id => reloadAuto(api('POST', `/api/home/suggestions/${encodeURIComponent(id)}/dismiss`));

  function toggleSetting(el) {
    if (!isAdmin()) return G.showToast('Only an admin can change this', 'info');
    el.classList.toggle('on');
  }

  async function saveAway() {
    const settings = {
      vacation_start: $('ha-vac-start').value, vacation_end: $('ha-vac-end').value,
      left_on_minutes: Number($('ha-lefton').value || 0),
    };
    document.querySelectorAll('#ha-away-card .toggle[data-setting]').forEach(t => {
      settings[t.dataset.setting] = t.classList.contains('on');
    });
    try {
      await api('POST', '/api/home/settings', { settings });
      msg('ha-away-msg', 'Saved.', true);
    } catch (e) { msg('ha-away-msg', errText(e), false); }
  }

  // ── Insights page ─────────────────────────────────────────
  async function loadInsights() {
    loadDigest(false);
    try {
      const all = await api('GET', '/api/home/insights?days=7&limit=60');
      renderActivity(all.activity);
    } catch (e) { G.showToast(errText(e), 'error'); }
  }

  async function loadDigest(refresh) {
    $('ha-digest-text').innerHTML = '<span class="ha-sub">Writing today’s summary…</span>';
    try {
      const d = await api('GET', `/api/home/digest${refresh ? '?refresh=true' : ''}`);
      $('ha-digest-text').textContent = d.text;
      $('ha-digest-meta').textContent = `${d.source === 'nim' ? 'Written by NVIDIA NIM from local counts' : 'Local summary'} · ${clock(d.generated_at)}`;
    } catch (e) { $('ha-digest-text').textContent = errText(e); }
  }

  function cause(e) {
    if (e.rule_id) return `rule · “${e.rule || e.rule_id}”`;
    const s = e.source || 'manual';
    if (s.startsWith('scene:')) return 'scene';
    if (s.startsWith('schedule:')) return 'schedule';
    return { away: 'left home', vacation: 'vacation lighting', assistant: 'Narada', manual: 'manual' }[s] || s;
  }

  function renderActivity(entries) {
    $('ha-activity').innerHTML = entries.length ? entries.map(e => `
      <div class="ha-act ${e.ok ? '' : 'fail'}">
        <span class="ha-act-time">${whenLabel(e.ts)}</span>
        <span class="ha-act-what"><b>${esc(e.device_name)}</b> ${esc(e.action)}${e.ok ? '' : ` — failed: ${esc(e.reason)}`}</span>
        <span class="ha-sub">${esc(cause(e))}${e.actor ? ' · ' + esc(e.actor) : ''}</span>
      </div>`).join('') : '<div class="ha-empty">Nothing has been switched yet.</div>';
  }

  // ── AI settings (System page, admin) ──────────────────────
  async function loadAI() {
    if (!isAdmin()) return;
    try {
      const st = await api('GET', '/api/home/ai');
      const nim = st.nim || {}, dec = st.decision || {};
      const pill = $('ai-status-pill');
      pill.textContent = nim.configured ? (nim.last_error ? 'Error' : 'Configured') : 'No key';
      pill.className = 'ha-pill ' + (nim.configured ? (nim.last_error ? 'bad' : 'ok') : 'warn');
      $('ai-nim-model').value = (nim.models || [])[0] || '';
      $('ai-nim-fallbacks').value = (nim.models || []).slice(1).join(', ');
      $('ai-nim-meta').textContent = nim.configured
        ? `${nim.calls} calls · ${nim.tokens_used} tokens this run${nim.last_model ? ` · last answered by ${nim.last_model} in ${nim.last_latency_s}s` : ''}${nim.last_error ? ` · last error: ${nim.last_error}` : ''}`
        : 'Without a key, Narada still switches devices, runs scenes and answers simple questions on the Pi.';
      $('ai-thr').value = Math.round((dec.threshold || 0.85) * 100);
      $('ai-thr-val').textContent = (dec.threshold || 0.85).toFixed(2);
      const lanes = st.lanes || {};
      $('ai-decision-meta').textContent = `${dec.jev_configured ? 'Jev key set' : dec.router_configured ? 'Routing model on the Pi' : 'Local engine'} · handled on-device ${lanes.fast || 0}, by NIM ${lanes.agent || 0}, offline ${lanes.local || 0}${dec.last_latency_ms != null ? ` · last decision ${dec.last_latency_ms} ms (${dec.last_backend})` : ''}${dec.jev_errors ? ` · Jev errors ${dec.jev_errors}` : ''}`;
    } catch (_) {}
  }

  async function saveAI() {
    const body = {};
    const key = $('ai-nim-key').value.trim();
    if (key) body.nim_api_key = key;
    body.nim_model = $('ai-nim-model').value.trim();
    body.nim_fallback_models = $('ai-nim-fallbacks').value.trim();
    const jev = $('ai-jev-key').value;
    if (jev) body.jev_api_key = jev.trim();
    body.decision_threshold = Number($('ai-thr').value) / 100;
    try {
      await api('POST', '/api/home/ai', body);
      $('ai-nim-key').value = ''; $('ai-jev-key').value = '';
      msg('ai-msg', 'AI settings saved.', true);
      loadAI();
    } catch (e) { msg('ai-msg', errText(e), false); }
  }

  async function testAI() {
    msg('ai-msg', 'Testing…', true);
    try {
      const r = await api('POST', '/api/home/ai/test');
      msg('ai-msg', r.ok ? `Working — ${r.model} answered in ${r.latency_s}s.` : `Not working: ${r.error}`, r.ok);
      loadAI();
    } catch (e) { msg('ai-msg', errText(e), false); }
  }

  return {
    onLogin, onNav, onState, decorateChatReply,
    proposalHtml: p => proposalCard(p, true),
    toggleDevice, allOff, runScene, deleteScene, noticeAction, dismissNotice, instruct,
    openSceneEditor, pickStep, saveScene,
    onKindChange, addDevice, setWatts, setEnabled, deleteDevice,
    confirmProposal, discardProposal, toggleRule, deleteRule,
    toggleSchedule, deleteSchedule, acceptSuggestion, dismissSuggestion,
    toggleDay, onSchedMode, addSchedule, toggleSetting, saveAway,
    loadDigest, loadAI, saveAI, testAI,
    runShortcut, cancelShortcut, toggleShortcut, deleteShortcut,
    loadCaptures, filterCaptures, captureNow, keepCapture, deleteCapture,
    // After a card is confirmed in the conversation, whatever page is open reloads.
    refresh: () => onNav(_page),
  };
})();
window.H = H;

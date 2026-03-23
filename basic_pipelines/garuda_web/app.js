/* ============================================================
   Garuda — SPA logic
   ============================================================ */
const G = (() => {

  // ── State ────────────────────────────────────────────────
  let _session = null;   // { role, username, display_name }
  let _token   = null;   // session token for cross-origin auth
  let _ws      = null;
  let _pendingAdmin = null;  // { username } during OTP flow
  let _prevAlertActive = false;
  let _lastDetInfo = '';
  let _recentDets  = [];
  let _privacyOn = true;
  let _allLogs   = [];
  let _cfg       = {};

  const SWATCH_COLORS = [
    '#2997ff','#34c759','#ff3b30','#ff9f0a',
    '#af52de','#5e5ce6','#00c7be','#ff375f','#636366'
  ];

  const MODE_CFG = [
    { key:'privacy',   label:'Privacy Blur',     icon:'◉', cls:'mode-blue'   },
    { key:'night',     label:'Night Mode',        icon:'◑', cls:'mode-purple' },
    { key:'dnd',       label:'Do Not Disturb',    icon:'◯', cls:'mode-warn'   },
    { key:'idle',      label:'Idle',              icon:'⊟', cls:'mode-muted'  },
    { key:'email_off', label:'Email Alerts Off',  icon:'◫', cls:'mode-muted'  },
    { key:'emergency', label:'Emergency',         icon:'△', cls:'mode-danger' },
  ];

  // ── Backend URL config ───────────────────────────────────
  function getBackend() {
    const h = location.hostname;
    const isLocal = h === 'localhost' || h === '127.0.0.1'
                 || h.startsWith('192.168.') || h.startsWith('10.')
                 || h.startsWith('172.');
    if (isLocal) return '';
    if (h === 'garuda.veeramanikanta.in') return 'https://api.veeramanikanta.in';
    return localStorage.getItem('garuda_backend') || '';
  }

  function openBackendConfig() {
    $('m-bk-url').value = localStorage.getItem('garuda_backend') || '';
    $('m-bk-msg').classList.add('hidden');
    show('m-backend');
  }

  async function saveBackendConfig() {
    let url = ($('m-bk-url').value || '').trim().replace(/\/$/, '');
    if (!url) { showEl('m-bk-msg', 'Enter a backend URL.', false); return; }
    if (!/^https?:\/\//.test(url)) url = 'http://' + url;
    showEl('m-bk-msg', 'Testing connection…', true);
    try {
      const r = await fetch(url + '/api/users-public', { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      localStorage.setItem('garuda_backend', url);
      updateBackendStatus(url);
      closeModal('m-backend');
    } catch(e) {
      showEl('m-bk-msg', 'Cannot reach backend: ' + (e.message || 'timeout'), false);
    }
  }

  async function updateBackendStatus(url) {
    const dot = $('bk-dot');
    const lbl = $('bk-label');
    if (!dot || !lbl) return;
    const isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
    const displayHost = url
      ? (() => { try { return new URL(url).hostname; } catch(_){ return url; } })()
      : (isLocal ? 'localhost' : 'No backend');
    lbl.textContent = displayHost;
    dot.className = 'bk-dot';
    const pingUrl = (url || '') + '/api/users-public';
    try {
      const r = await fetch(pingUrl, { method:'GET', credentials:'omit', signal: AbortSignal.timeout(5000) });
      dot.className = 'bk-dot' + (r.ok ? ' ok' : '');
    } catch(_) {
      dot.className = 'bk-dot';
    }
  }

  // ── Boot ─────────────────────────────────────────────────
  async function init() {
    buildSwatches('m-swatches');
    const backend = getBackend();
    updateBackendStatus(backend);
    const isLocal = ['localhost','127.0.0.1'].includes(location.hostname);
    if (backend) _token = localStorage.getItem('garuda_token') || null;

    // Try to restore session from previous visit
    const canRestore = isLocal || !!_token;
    if (canRestore) {
      try {
        const session = await api('GET', '/api/session');
        _session = session;
        afterLogin();
        return;
      } catch(e) {
        localStorage.removeItem('garuda_token');
        _token = null;
      }
    }

    if (!backend && !isLocal) openBackendConfig();
    showLoginView('lv-main');
    renderHeatmap();
  }

  // ── Login view switcher ───────────────────────────────────
  function showLoginView(viewId) {
    ['lv-main','lv-admin-1','lv-admin-2','lv-forgot'].forEach(id => {
      const el = $(id);
      if (el) el.classList.toggle('hidden', id !== viewId);
    });
  }

  function goAdminFlow() {
    showLoginView('lv-admin-1');
    if ($('adm-user')) $('adm-user').value = '';
    $('adm-err-1')?.classList.add('hidden');
    setTimeout(() => $('adm-user')?.focus(), 50);
  }

  function backToMain() {
    showLoginView('lv-main');
    $('li-err')?.classList.add('hidden');
  }

  function backToAdminStep1() {
    showLoginView('lv-admin-1');
    $('adm-err-1')?.classList.add('hidden');
  }

  async function sendAdminOTP() {
    const un = ($('adm-user')?.value || '').trim();
    const pw = $('adm-pass')?.value || '';
    const errEl = $('adm-err-1');
    if (!un || !pw) { showLoginErr(errEl, 'Enter username and password.'); return; }
    try {
      await api('POST', '/api/admin/send-otp', { username: un, password: pw });
      _pendingAdmin = { username: un };
      if ($('adm-otp')) $('adm-otp').value = '';
      $('adm-err-2')?.classList.add('hidden');
      showLoginView('lv-admin-2');
      setTimeout(() => $('adm-otp')?.focus(), 50);
    } catch(e) {
      showLoginErr(errEl, extractError(e));
    }
  }

  async function verifyAdminOTP() {
    const otp = ($('adm-otp')?.value || '').trim();
    const errEl = $('adm-err-2');
    if (!otp || !_pendingAdmin) { showLoginErr(errEl, 'Enter the 6-digit OTP.'); return; }
    try {
      const res = await api('POST', '/api/admin/verify-otp',
                            { username: _pendingAdmin.username, otp });
      _session = res;
      if (res.token && getBackend()) {
        _token = res.token;
        localStorage.setItem('garuda_token', _token);
      }
      _pendingAdmin = null;
      afterLogin();
    } catch(e) {
      showLoginErr(errEl, extractError(e));
    }
  }

  async function submitLogin() {
    const un = ($('li-user')?.value || '').trim();
    const pw = $('li-pass')?.value || '';
    const errEl = $('li-err');
    errEl?.classList.add('hidden');
    if (!un || !pw) { showLoginErr(errEl, 'Enter username and password.'); return; }
    try {
      const res = await api('POST', '/api/login', { username: un, password: pw });
      _session = res;
      if (res.token && getBackend()) {
        _token = res.token;
        localStorage.setItem('garuda_token', _token);
      }
      afterLogin();
    } catch(e) {
      showLoginErr(errEl, extractError(e));
    }
  }

  function afterLogin() {
    $('app').classList.add('logged-in');
    $('hdr-user').textContent = _session.display_name || _session.username;
    if (_session.role === 'admin') show('admin-nav');
    nav('dashboard', document.querySelector('[data-page="dashboard"]'));
    renderHeatmap();
    connectWS();
    if (_session.role === 'admin') loadCfg();
  }

  async function logout() {
    try { await api('POST', '/api/logout', {}); } catch(_) {}
    _session = null; _token = null;
    _recentDets = []; _prevAlertActive = false; _lastDetInfo = '';
    localStorage.removeItem('garuda_token');
    if (_ws) { _ws.close(); _ws = null; }
    $('app').classList.remove('logged-in');
    hide('admin-nav');
    // Stop camera
    const camOv = $('camera-overlay');
    if (camOv) camOv.classList.add('hidden');
    const camImg = $('cam-img');
    if (camImg) { camImg.src = ''; camImg.style.display = 'none'; }
    // Reset login card
    if ($('li-user')) $('li-user').value = '';
    if ($('li-pass')) $('li-pass').value = '';
    $('li-err')?.classList.add('hidden');
    showLoginView('lv-main');
  }

  // ── Forgot password ───────────────────────────────────────
  function goForgot() {
    const un = ($('li-user')?.value || '').trim();
    showLoginView('lv-forgot');
    if ($('fp-user')) $('fp-user').value = un;
    $('fp-otp-block')?.classList.add('hidden');
    $('fp-msg')?.classList.add('hidden');
    if ($('fp-btn')) {
      $('fp-btn').textContent = 'Send OTP';
      $('fp-btn').onclick = G.sendForgotOTP;
    }
  }

  async function sendForgotOTP() {
    const un = ($('fp-user')?.value || '').trim();
    if (!un) { showEl('fp-msg', 'Enter your username.', false); return; }
    try {
      const r = await api('POST', '/api/forgot/send-otp', { username: un });
      if (r.bypass_otp) showEl('fp-msg', `Dev OTP: ${r.bypass_otp}`, false);
      else showEl('fp-msg', 'OTP sent to alert email.', true);
      const fpBlock = $('fp-otp-block');
      if (fpBlock) { fpBlock.classList.remove('hidden'); fpBlock.style.display = 'flex'; }
      if ($('fp-btn')) {
        $('fp-btn').textContent = 'Reset Password';
        $('fp-btn').onclick = G.doReset;
      }
    } catch(e) { showEl('fp-msg', e.detail || 'Failed.', false); }
  }

  async function doReset() {
    const otp = ($('fp-otp')?.value || '').trim();
    const pw  = $('fp-newpass')?.value || '';
    if (!otp || !pw) { showEl('fp-msg', 'Enter OTP and new password.', false); return; }
    try {
      await api('POST', '/api/forgot/reset', { otp, new_password: pw });
      showEl('fp-msg', 'Password reset! You can now sign in.', true);
      setTimeout(() => showLoginView('lv-main'), 2000);
    } catch(e) { showEl('fp-msg', e.detail || 'Invalid OTP.', false); }
  }

  // ── Camera overlay ────────────────────────────────────────
  function toggleCamera() {
    const overlay = $('camera-overlay');
    if (!overlay) return;
    const isHidden = overlay.classList.contains('hidden');
    const camImg    = $('cam-img');
    const camOffline = $('cam-offline');
    const camStatus  = $('cam-status-txt');
    if (isHidden) {
      // Start MJPEG stream
      if (camImg) {
        camImg.style.display = 'none';
        if (camOffline) camOffline.style.display = 'flex';
        if (camStatus) camStatus.textContent = 'Connecting...';
        camImg.onload = () => {
          camImg.style.display = 'block';
          if (camOffline) camOffline.style.display = 'none';
          if (camStatus) camStatus.textContent = 'Live';
        };
        camImg.onerror = () => {
          camImg.style.display = 'none';
          if (camOffline) camOffline.style.display = 'flex';
          if (camStatus) camStatus.textContent = 'Unavailable';
        };
        camImg.src = (getBackend() || '') + '/stream?t=' + Date.now();
      }
      overlay.classList.remove('hidden');
    } else {
      // Stop stream
      if (camImg) { camImg.src = ''; camImg.style.display = 'none'; }
      overlay.classList.add('hidden');
    }
  }

  // ── Alert activity heatmap (localStorage) ────────────────
  function recordActivity() {
    const today = new Date().toISOString().slice(0, 10);
    let activity;
    try { activity = JSON.parse(localStorage.getItem('garuda_activity') || '{}'); }
    catch(_) { activity = {}; }
    activity[today] = (activity[today] || 0) + 1;
    localStorage.setItem('garuda_activity', JSON.stringify(activity));
    renderHeatmap();
  }

  function renderHeatmap() {
    const container = $('heatmap');
    if (!container) return;
    let activity;
    try { activity = JSON.parse(localStorage.getItem('garuda_activity') || '{}'); }
    catch(_) { activity = {}; }

    // 84 cells: 12 weeks × 7 days, ending today
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const cells = [];
    for (let i = 83; i >= 0; i--) {
      const d = new Date(today); d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      const count = activity[key] || 0;
      const level = count >= 5 ? 3 : count >= 3 ? 2 : count >= 1 ? 1 : 0;
      cells.push({ key, count, level });
    }

    container.innerHTML = '';
    const grid = mk('div', 'hm-grid');
    cells.forEach(c => {
      const cell = mk('div', `hm-cell hm-${c.level}`);
      cell.title = `${c.key}: ${c.count} alert${c.count !== 1 ? 's' : ''}`;
      grid.appendChild(cell);
    });
    container.appendChild(grid);
  }

  // ── Recent detections ────────────────────────────────────
  function maybeAddDetection(detInfo) {
    if (!detInfo || detInfo === 'No detections.' || detInfo === _lastDetInfo) return;
    _lastDetInfo = detInfo;
    const time = new Date().toLocaleTimeString('en-US',
      { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const lines = detInfo.split('\n').filter(l => l.trim() && l.trim() !== 'No detections.');
    lines.forEach(label => {
      _recentDets.unshift({ label: label.trim(), time });
    });
    if (_recentDets.length > 20) _recentDets.length = 20;
    renderRecentDets();
  }

  function renderRecentDets() {
    const container = $('recent-dets');
    if (!container) return;
    if (!_recentDets.length) {
      container.innerHTML = '<div class="det-empty">No detections yet this session</div>';
      return;
    }
    container.innerHTML = '';
    _recentDets.forEach(d => {
      const item = mk('div', 'det-item');
      item.innerHTML = `
        <span class="det-time">${esc(d.time)}</span>
        <div class="det-dot"></div>
        <span class="det-label">${esc(d.label)}</span>`;
      container.appendChild(item);
    });
  }

  // ── Navigation ────────────────────────────────────────────
  function nav(pageId, navEl) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const pg = $('page-' + pageId); if (pg) pg.classList.add('active');
    if (navEl) navEl.classList.add('active');
    closeMobileMenu();
    if (pageId === 'a-users')    loadUsers();
    if (pageId === 'a-email')    loadEmailCfg();
    if (pageId === 'a-settings') loadSysCfg();
    if (pageId === 'a-logs')     renderLogs();
    if (pageId === 'a-cmds')     loadCmds();
  }

  // ── Mobile sidebar ────────────────────────────────────────
  function toggleMenu() {
    const sidebar = $('sidebar');
    const overlay = $('sidebar-overlay');
    const isOpen  = sidebar.classList.contains('open');
    sidebar.classList.toggle('open', !isOpen);
    overlay.classList.toggle('visible', !isOpen);
  }
  function closeMobileMenu() {
    $('sidebar')?.classList.remove('open');
    $('sidebar-overlay')?.classList.remove('visible');
  }

  // ── WebSocket ─────────────────────────────────────────────
  function connectWS() {
    if (_ws) _ws.close();
    const base = getBackend();
    const tok = _token || (base ? localStorage.getItem('garuda_token') : null);
    let wsUrl;
    if (base) {
      wsUrl = base.replace(/^http/, 'ws').replace(/\/$/, '') + '/ws' + (tok ? `?token=${tok}` : '');
    } else {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      wsUrl = `${proto}://${location.host}/ws`;
    }
    _ws = new WebSocket(wsUrl);
    _ws.onmessage = e => tick(JSON.parse(e.data));
    _ws.onclose   = () => setTimeout(connectWS, 3000);
  }

  function tick(s) {
    // Alert banner
    const banner = $('alert-banner');
    if (s.alert_active) {
      banner.classList.add('visible');
      $('pipeline-dot').className = 'hdr-dot alert';
      $('pipeline-label').textContent = 'Alert';
      if (!_prevAlertActive) recordActivity();
    } else {
      banner.classList.remove('visible');
      $('pipeline-dot').className = 'hdr-dot online';
      $('pipeline-label').textContent = 'Online';
      if (_prevAlertActive) _lastDetInfo = ''; // reset so next alert adds fresh entries
    }
    _prevAlertActive = !!s.alert_active;

    // Modes
    renderModes(s.modes);

    // Security status card
    const card = $('status-card');
    if (card) {
      if (s.alert_active) {
        card.classList.add('alert');
        setText('status-label', 'ALERT');
        const info = (s.detection_info || '').replace('No detections.', '').trim();
        setText('status-desc', info || 'Threat detected');
      } else {
        card.classList.remove('alert');
        setText('status-label', 'ALL CLEAR');
        setText('status-desc', 'No threats detected');
      }
      setText('status-last', s.last_alert ? timeSince(new Date(s.last_alert)) : 'Never');
    }

    // Stats
    setText('s-uptime', fmtUptime(s.uptime_s));
    setText('s-det', String(s.detections_today || 0));
    setText('s-alert', s.last_alert ? timeSince(new Date(s.last_alert)) : 'None');
    setText('s-thr', s.detection_threshold ? s.detection_threshold.toFixed(2) : '—');
    setText('s-pipeline', s.alert_active ? 'Alert' : 'Active');

    // Recent detections
    if (s.alert_active && s.detection_info) maybeAddDetection(s.detection_info);

    // Console
    const logText = (s.system_log || []).join('\n');
    ['sys-console', 'narada-console'].forEach(id => {
      const con = $(id); if (!con) return;
      const atBot = con.scrollTop + con.clientHeight >= con.scrollHeight - 8;
      con.textContent = logText;
      if (atBot) con.scrollTop = con.scrollHeight;
    });

    // Narada
    renderLog('narada-vlog',  s.voice_log      || [], false);
    renderLog('narada-resp',  s.voice_responses || [], true);

    // Admin logs live update
    _allLogs = s.system_log || [];
    renderLogs();
    const av = $('a-vlog');
    if (av) {
      av.innerHTML = [...(s.voice_log||[]), ...(s.voice_responses||[])]
        .map(l => `<div class="log-line">${esc(l)}</div>`).join('');
      av.scrollTop = av.scrollHeight;
    }
  }

  function fmtUptime(secs) {
    if (secs === undefined || secs === null) return '—';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function timeSince(date) {
    const secs = Math.floor((new Date() - date) / 1000);
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  function renderLog(id, lines, isResp) {
    const el = $(id); if (!el) return;
    const atBot = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
    el.innerHTML = lines.map(l =>
      `<div class="log-line${isResp ? ' response' : ''}">${esc(l)}</div>`
    ).join('');
    if (atBot) el.scrollTop = el.scrollHeight;
  }

  function renderModes(modes) {
    const grid   = $('modes-pills');
    const hpills = $('header-pills');
    if (!grid) return;

    // Dashboard list rows
    grid.innerHTML = '';
    MODE_CFG.forEach(m => {
      const isOn = !!modes[m.key];
      const row = mk('div', `mode-row${isOn ? ' on' : ''} ${m.cls}`);
      row.innerHTML = `
        <div class="mode-row-icon">${m.icon}</div>
        <span class="mode-row-label">${m.label}</span>
        <div class="mode-toggle${isOn ? ' on' : ''}"></div>`;
      row.onclick = () => toggleMode(m.key, isOn);
      grid.appendChild(row);
    });

    // Header pills — only active modes
    if (hpills) {
      hpills.innerHTML = '';
      MODE_CFG.filter(m => modes[m.key]).forEach(m => {
        const p = mk('span', 'mode-pill active');
        p.textContent = m.label;
        hpills.appendChild(p);
      });
    }
  }

  async function toggleMode(mode, currentVal) {
    try { await api('POST', '/api/modes', { mode, value: !currentVal }); }
    catch(e) { console.error(e); }
  }

  // ── Admin: load config ────────────────────────────────────
  async function loadCfg() {
    try { _cfg = await api('GET', '/api/config'); } catch(_) {}
  }

  // ── Admin: Users ──────────────────────────────────────────
  async function loadUsers() {
    try {
      const data = await api('GET', '/api/users');
      const tb = $('u-tbody'); tb.innerHTML = '';
      Object.entries(data).forEach(([un, u]) => {
        const tr = mk('tr');
        tr.innerHTML = `
          <td style="font-family:var(--mono);font-size:12px">${esc(un)}</td>
          <td>${esc(u.display_name)}</td>
          <td><span class="mode-pill${u.role==='admin'?' active blue':''}"
              style="cursor:default;font-size:11px">${u.role}</span></td>
          <td><span class="color-dot" style="background:${u.box_color}"></span></td>
          <td class="flex gap-8">
            <button class="btn btn-ghost btn-sm"
              onclick='G._editUser(${JSON.stringify(un)},${JSON.stringify(u.display_name)},${JSON.stringify(u.box_color)})'>Edit</button>
            ${un !== 'admin' ? `<button class="btn btn-danger btn-sm"
              onclick='G._delUser(${JSON.stringify(un)})'>Delete</button>` : ''}
          </td>`;
        tb.appendChild(tr);
      });
    } catch(e) { console.error(e); }
  }

  function openAddUser() {
    $('m-uname').value = ''; $('m-upass').value = ''; $('m-dname').value = '';
    $('m-color').value = '#2997ff'; $('m-role').value = 'user';
    $('m-add-err').classList.add('hidden');
    show('m-add-user');
  }

  async function addUser() {
    const un = val('m-uname'), pw = val('m-upass'), dn = val('m-dname');
    const color = $('m-color').value, role = $('m-role').value;
    const err = $('m-add-err');
    if (!un || !pw) { showMsg(err, 'Username and password required.', false); return; }
    try {
      await api('POST', '/api/users/add',
        { username: un, password: pw, display_name: dn || un, box_color: color, role });
      closeModal('m-add-user'); loadUsers();
    } catch(e) { showMsg(err, e.detail || 'Failed.', false); }
  }

  function _editUser(un, dn, col) {
    $('m-edit-un').value = un;
    $('m-edit-title').textContent = `Edit — ${un}`;
    $('m-edit-dn').value = dn; $('m-edit-pw').value = ''; $('m-edit-col').value = col;
    $('m-edit-err').classList.add('hidden');
    show('m-edit-user');
  }

  async function saveUser() {
    const un = $('m-edit-un').value, dn = val('m-edit-dn');
    const pw = val('m-edit-pw'), col = $('m-edit-col').value;
    const err = $('m-edit-err');
    const payload = { username: un, display_name: dn, box_color: col };
    if (pw) payload.new_password = pw;
    try {
      await api('POST', '/api/users/update', payload);
      closeModal('m-edit-user'); loadUsers();
    } catch(e) { showMsg(err, e.detail || 'Failed.', false); }
  }

  async function _delUser(un) {
    if (!confirm(`Delete user "${un}"? This cannot be undone.`)) return;
    try { await api('POST', '/api/users/delete', { username: un }); loadUsers(); }
    catch(e) { alert(e.detail || 'Failed.'); }
  }

  // ── Admin: Email ──────────────────────────────────────────
  async function loadEmailCfg() {
    try {
      const cfg = await api('GET', '/api/config'); _cfg = cfg;
      $('e-sender').value = cfg.email_sender || '';
      $('e-pass').value = '';
      $('e-recip').value = (cfg.email_recipients || []).join(', ');
      $('e-cool').value = cfg.email_cooldown || 60;
    } catch(e) {}
  }

  async function saveEmail() {
    const payload = {
      email_sender: val('e-sender'),
      email_recipients: val('e-recip').split(',').map(s => s.trim()).filter(Boolean),
      email_cooldown: parseInt($('e-cool').value) || 60,
    };
    const pw = val('e-pass'); if (pw) payload.email_sender_pass = pw;
    try { await api('POST', '/api/config', payload); showEl('e-msg', 'Saved.', true); }
    catch(e) { showEl('e-msg', e.detail || 'Failed.', false); }
  }

  async function testEmail() {
    showEl('e-msg', 'Sending…', true);
    try {
      const r = await api('POST', '/api/email/test', {});
      showEl('e-msg', r.ok ? 'Test email sent!' : 'Failed: ' + r.error, r.ok);
    } catch(e) { showEl('e-msg', e.detail || 'Failed.', false); }
  }

  // ── Admin: System settings ────────────────────────────────
  async function loadSysCfg() {
    try {
      const cfg = await api('GET', '/api/config'); _cfg = cfg;
      const t = Math.round((cfg.detection_threshold || 0.3) * 100);
      $('thr-slider').value = t;
      $('thr-val').textContent = (t / 100).toFixed(2);
      _privacyOn = cfg.privacy !== undefined ? cfg.privacy : true;
      $('priv-toggle').className = 'toggle' + (_privacyOn ? ' on' : '');
    } catch(e) {}
  }

  function togglePrivacy() {
    _privacyOn = !_privacyOn;
    $('priv-toggle').className = 'toggle' + (_privacyOn ? ' on' : '');
  }

  async function saveSettings() {
    const thr = parseInt($('thr-slider').value) / 100;
    const dl = val('danger-lbl') || undefined;
    try {
      await api('POST', '/api/config',
        { detection_threshold: thr, privacy: _privacyOn, ...(dl ? { danger_label: dl } : {}) });
      showEl('sys-msg', 'Settings saved.', true);
    } catch(e) { showEl('sys-msg', e.detail || 'Failed.', false); }
  }

  // ── Admin: Logs ───────────────────────────────────────────
  function renderLogs() {
    const q = (val('log-q') || '').toLowerCase();
    const el = $('a-syslog'); if (!el) return;
    const lines = _allLogs.filter(l => !q || l.toLowerCase().includes(q));
    el.textContent = lines.join('\n');
    el.scrollTop = el.scrollHeight;
  }

  function filterLogs() { renderLogs(); }

  function exportLogs() {
    const blob = new Blob([_allLogs.join('\n')], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `garuda-logs-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
  }

  // ── Admin: Commands ───────────────────────────────────────
  async function loadCmds() {
    try {
      const cfg = await api('GET', '/api/config');
      const cmds = cfg.custom_voice_commands || {};
      const tb = $('cmd-tbody'); tb.innerHTML = '';
      if (!Object.keys(cmds).length) {
        tb.innerHTML = '<tr><td colspan="3" style="color:var(--t3);text-align:center;padding:20px">No custom commands yet.</td></tr>';
        return;
      }
      Object.entries(cmds).forEach(([phrase, resp]) => {
        const tr = mk('tr');
        tr.innerHTML = `
          <td style="font-family:var(--mono);font-size:12px;color:var(--accent)">${esc(phrase)}</td>
          <td style="color:var(--t2)">${esc(resp)}</td>
          <td><button class="btn btn-ghost btn-sm"
            onclick='G._delCmd(${JSON.stringify(phrase)})'>Delete</button></td>`;
        tb.appendChild(tr);
      });
    } catch(e) {}
  }

  function openAddCmd() {
    $('m-phrase').value = ''; $('m-resp').value = '';
    show('m-add-cmd');
  }

  async function addCmd() {
    const phrase = val('m-phrase').toLowerCase();
    const resp = val('m-resp');
    if (!phrase || !resp) { alert('Enter both fields.'); return; }
    try {
      await api('POST', '/api/config/command/add', { phrase, response: resp });
      closeModal('m-add-cmd'); loadCmds();
    } catch(e) { alert(e.detail || 'Failed.'); }
  }

  async function _delCmd(phrase) {
    if (!confirm(`Delete "${phrase}"?`)) return;
    try { await api('POST', '/api/config/command/delete', { phrase }); loadCmds(); }
    catch(e) { alert(e.detail || 'Failed.'); }
  }

  // ── Emergency Stop ────────────────────────────────────────
  async function emergencyStop() {
    if (!confirm('Stop the entire Garuda system now?')) return;
    await api('POST', '/api/emergency-stop', {});
  }

  // ── Color swatches ────────────────────────────────────────
  function buildSwatches(containerId) {
    const c = $(containerId); if (!c) return;
    SWATCH_COLORS.forEach(color => {
      const s = mk('div', 'swatch');
      s.style.background = color;
      s.onclick = () => { $('m-color').value = color; };
      c.appendChild(s);
    });
  }

  // ── Utils ─────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const val = id => ($(id)?.value || '').trim();
  const setText = (id, v) => { const e = $(id); if (e) e.textContent = v; };
  const show = id => $(id)?.classList.remove('hidden');
  const hide = id => $(id)?.classList.add('hidden');
  const mk = (tag, cls = '') => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  };
  const esc = s => String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const closeModal = id => hide(id);

  function extractError(e) {
    if (!e) return 'An error occurred.';
    if (typeof e === 'string') return e;
    if (e.detail) {
      if (typeof e.detail === 'string') return e.detail;
      if (Array.isArray(e.detail))
        return e.detail.map(d => d.msg || d.message || String(d)).join('; ');
      if (typeof e.detail === 'object') return e.detail.msg || JSON.stringify(e.detail);
    }
    if (e.message) return e.message;
    return 'An error occurred.';
  }

  function showLoginErr(el, txt) {
    if (!el) return;
    el.textContent = txt;
    el.classList.remove('hidden');
  }

  function openDocs() { show('m-docs'); }
  function showMsg(el, txt, ok) {
    el.textContent = txt;
    el.className = 'msg ' + (ok ? 'ok' : 'err');
    el.classList.remove('hidden');
  }
  function showEl(id, txt, ok) { showMsg($(id), txt, ok); }

  async function api(method, url, body) {
    const base = getBackend();
    const fullUrl = base ? base.replace(/\/$/, '') + url : url;
    const headers = { 'Content-Type': 'application/json' };
    const tok = _token || (base ? localStorage.getItem('garuda_token') : null);
    if (tok) headers['X-Garuda-Token'] = tok;
    const opts = { method, headers, credentials: base ? 'omit' : 'include' };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(fullUrl, opts);
    const d = await r.json();
    if (!r.ok) throw d;
    return d;
  }

  // ── Public API ────────────────────────────────────────────
  return {
    init,
    submitLogin, logout,
    goAdminFlow, backToMain, backToAdminStep1, sendAdminOTP, verifyAdminOTP,
    goForgot, sendForgotOTP, doReset,
    nav, toggleMode, emergencyStop,
    openBackendConfig, saveBackendConfig,
    toggleMenu, closeMobileMenu,
    toggleCamera, openDocs,
    loadUsers, openAddUser, addUser, _editUser, saveUser, _delUser,
    loadEmailCfg, saveEmail, testEmail,
    loadSysCfg, togglePrivacy, saveSettings,
    filterLogs, exportLogs,
    loadCmds, openAddCmd, addCmd, _delCmd,
    closeModal,
  };
})();

document.addEventListener('DOMContentLoaded', G.init);

// Close modal on overlay click
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) e.target.classList.add('hidden');
});

// Enter key for login views
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const lv1 = document.getElementById('lv-main');
  const lv2 = document.getElementById('lv-admin-1');
  const lv3 = document.getElementById('lv-admin-2');
  if (lv1 && !lv1.classList.contains('hidden')) G.submitLogin();
  else if (lv2 && !lv2.classList.contains('hidden')) G.sendAdminOTP();
  else if (lv3 && !lv3.classList.contains('hidden')) G.verifyAdminOTP();
});

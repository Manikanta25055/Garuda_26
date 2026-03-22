/* ============================================================
   Garuda Web — Frontend SPA Logic
   ============================================================ */

const G = (() => {

  // ── State ──────────────────────────────────────────────
  let session = null;  // { role, username, display_name }
  let ws = null;
  let _adminUsername = null;
  let _adminPending = null;  // { username } while waiting for OTP
  let _privacyOn = true;
  let _allLogs = [];
  let _cfg = {};

  const SWATCH_COLORS = [
    "#1565c0","#2e7d32","#6a1b9a","#00838f",
    "#f57f17","#4527a0","#ad1457","#c62828","#00695c"
  ];

  // ── Init ───────────────────────────────────────────────
  async function init() {
    buildColorSwatches();
    await loadProfileCards();
  }

  async function loadProfileCards() {
    const container = document.getElementById('profile-cards');
    container.innerHTML = '';
    try {
      const res = await fetch('/api/users-public');
      const users = await res.json();
      users.forEach(u => {
        const card = document.createElement('div');
        card.className = 'profile-card';
        card.style.background = u.box_color;
        card.innerHTML = `<div class="avatar">${u.display_name[0].toUpperCase()}</div><span>${u.display_name}</span>`;
        card.onclick = () => showLoginForm(u.username, u.display_name, false);
        container.appendChild(card);
      });
    } catch(e) {
      container.innerHTML = '<p style="color:var(--text-2)">Could not load users.</p>';
    }
    // Admin card always at end
    const adminCard = document.createElement('div');
    adminCard.className = 'admin-card';
    adminCard.innerHTML = `<div style="font-size:24px">🔐</div><span>Admin</span>`;
    adminCard.onclick = () => showLoginForm('', 'Admin', true);
    container.appendChild(adminCard);
  }

  function showCards() {
    hide('login-form');
    hide('admin-otp-form');
    hide('forgot-form');
    show('profile-cards');
    document.getElementById('forgot-link').style.display = 'none';
  }

  function showLoginForm(username, displayName, isAdmin) {
    hide('profile-cards');
    hide('admin-otp-form');
    hide('forgot-form');
    show('login-form');
    document.getElementById('login-form-title').textContent = isAdmin ? 'Admin Login' : displayName;
    document.getElementById('login-form-subtitle').textContent = isAdmin ? '' : `@${username}`;
    document.getElementById('login-username').value = username;
    document.getElementById('login-password').value = '';
    document.getElementById('login-error').classList.add('hidden');
    document.getElementById('forgot-link').style.display = isAdmin ? 'none' : 'inline-flex';
    document.getElementById('login-password').focus();

    // Store whether this is admin flow
    document.getElementById('login-form').dataset.isAdmin = isAdmin ? '1' : '0';
  }

  async function submitLogin() {
    const un = document.getElementById('login-username').value.trim();
    const pw = document.getElementById('login-password').value;
    const isAdmin = document.getElementById('login-form').dataset.isAdmin === '1';
    const errEl = document.getElementById('login-error');
    errEl.classList.add('hidden');

    if (!un || !pw) { showError(errEl, 'Enter username and password.'); return; }

    if (isAdmin) {
      // Admin flow: send OTP
      try {
        const res = await post('/api/admin/send-otp', { username: un, password: pw });
        if (!res.ok) { showError(errEl, res.error || 'Invalid credentials.'); return; }
        _adminPending = { username: un };
        hide('login-form');
        show('admin-otp-form');
        document.getElementById('admin-otp-input').value = '';
        document.getElementById('otp-error').classList.add('hidden');
        document.getElementById('admin-otp-input').focus();
        if (!res.ok && res.bypass_otp) {
          // Email failed but OTP shown for dev
          alert(`Email failed. Dev bypass OTP: ${res.bypass_otp}`);
        }
      } catch(e) { showError(errEl, 'Server error.'); }
    } else {
      // User flow: direct login
      try {
        const res = await post('/api/login', { username: un, password: pw });
        session = res;
        onLogin();
      } catch(e) {
        showError(errEl, e.detail || 'Invalid username or password.');
      }
    }
  }

  async function verifyAdminOTP() {
    const otp = document.getElementById('admin-otp-input').value.trim();
    const errEl = document.getElementById('otp-error');
    errEl.classList.add('hidden');
    if (!otp || !_adminPending) { showError(errEl, 'Enter the OTP.'); return; }
    try {
      const res = await post('/api/admin/verify-otp', { username: _adminPending.username, otp });
      session = res;
      _adminPending = null;
      onLogin();
    } catch(e) {
      showError(errEl, e.detail || 'Invalid OTP. Try again.');
    }
  }

  function onLogin() {
    document.getElementById('app').classList.add('logged-in');
    document.getElementById('header-username').textContent =
      session.display_name || session.username;
    if (session.role === 'admin') {
      show('admin-nav');
    }
    navigate('dashboard', document.querySelector('[data-page="dashboard"]'));
    connectWS();
    if (session.role === 'admin') {
      loadAdminData();
    }
  }

  async function logout() {
    await post('/api/logout', {});
    session = null;
    if (ws) { ws.close(); ws = null; }
    document.getElementById('app').classList.remove('logged-in');
    hide('admin-nav');
    showCards();
    loadProfileCards();
  }

  // ── Navigation ──────────────────────────────────────────
  function navigate(pageId, navEl) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const page = document.getElementById('page-' + pageId);
    if (page) page.classList.add('active');
    if (navEl) navEl.classList.add('active');

    // Lazy-load admin pages
    if (pageId === 'admin-users') loadUsers();
    if (pageId === 'admin-email') loadEmailSettings();
    if (pageId === 'admin-settings') loadSystemSettings();
    if (pageId === 'admin-logs') loadLogs();
    if (pageId === 'admin-narada') loadCommands();
  }

  // ── WebSocket ───────────────────────────────────────────
  function connectWS() {
    if (ws) ws.close();
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (e) => updateState(JSON.parse(e.data));
    ws.onclose = () => setTimeout(connectWS, 3000);
  }

  function updateState(s) {
    // Alert banner
    const banner = document.getElementById('alert-banner');
    if (s.alert_active) {
      banner.classList.add('visible');
      document.getElementById('pipeline-dot').className = 'status-dot alert';
    } else {
      banner.classList.remove('visible');
      document.getElementById('pipeline-dot').className = 'status-dot online';
    }

    // Mode pills on header & dashboard
    updateModePills(s.modes);

    // Stats
    setText('stat-uptime', s.uptime || '—');
    setText('stat-detections', s.detections_today || '0');
    setText('stat-last-alert', s.last_alert ? s.last_alert.substring(11,19) : '—');
    setText('stat-threshold', s.detection_threshold ? s.detection_threshold.toFixed(2) : '—');

    // Detection feed
    const feed = document.getElementById('detection-feed');
    if (feed) feed.textContent = s.detection_info || 'No detections.';

    // Console
    const cons = document.getElementById('system-console');
    if (cons) {
      const at_bottom = cons.scrollTop + cons.clientHeight >= cons.scrollHeight - 10;
      cons.textContent = (s.system_log || []).join('\n');
      if (at_bottom) cons.scrollTop = cons.scrollHeight;
    }

    // Narada logs
    const vlog = document.getElementById('voice-log');
    if (vlog) {
      vlog.innerHTML = (s.voice_log || []).map(l =>
        `<div class="voice-item">${esc(l)}</div>`).join('');
      vlog.scrollTop = vlog.scrollHeight;
    }
    const vresp = document.getElementById('voice-responses');
    if (vresp) {
      vresp.innerHTML = (s.voice_responses || []).map(l =>
        `<div class="voice-item response">${esc(l)}</div>`).join('');
      vresp.scrollTop = vresp.scrollHeight;
    }

    // Admin logs
    _allLogs = s.system_log || [];
    const adminLog = document.getElementById('admin-logs-text');
    if (adminLog) filterLogs();
    const adminVoice = document.getElementById('admin-voice-log');
    if (adminVoice) {
      adminVoice.innerHTML = [...(s.voice_log||[]), ...(s.voice_responses||[])].map(l =>
        `<div>${esc(l)}</div>`).join('');
    }
  }

  function updateModePills(modes) {
    const modeConf = [
      { key: 'dnd',       label: 'DND',       cls: '' },
      { key: 'night',     label: 'Night',     cls: 'yellow' },
      { key: 'emergency', label: 'EMERGENCY', cls: '' },
      { key: 'idle',      label: 'Idle',      cls: 'blue' },
      { key: 'email_off', label: 'Email Off', cls: '' },
      { key: 'privacy',   label: 'Privacy',   cls: 'blue' },
    ];
    const grid = document.getElementById('modes-grid');
    const header = document.getElementById('header-mode-pills');

    const makePill = (m, container) => {
      let el = container.querySelector(`[data-mode="${m.key}"]`);
      if (!el) {
        el = document.createElement('div');
        el.className = 'mode-pill';
        el.dataset.mode = m.key;
        el.innerHTML = `<span class="dot"></span> ${m.label}`;
        el.onclick = () => toggleMode(m.key, el);
        container.appendChild(el);
      }
      el.className = 'mode-pill' + (modes[m.key] ? ` active ${m.cls}` : '');
      el.innerHTML = `<span class="dot"></span> ${m.label}`;
      el.onclick = () => toggleMode(m.key, el);
    };

    if (grid) modeConf.forEach(m => makePill(m, grid));
    // Header only shows active modes
    if (header) {
      header.innerHTML = '';
      modeConf.filter(m => modes[m.key]).forEach(m => {
        const p = document.createElement('div');
        p.className = `mode-pill active ${m.cls}`;
        p.style.cssText = 'font-size:11px;padding:3px 10px';
        p.textContent = m.label;
        header.appendChild(p);
      });
    }
  }

  async function toggleMode(mode, el) {
    const current = el.classList.contains('active');
    try {
      await post('/api/modes', { mode, value: !current });
    } catch(e) { console.error(e); }
  }

  // ── Admin: Users ────────────────────────────────────────
  async function loadUsers() {
    try {
      const data = await get('/api/users');
      const tbody = document.getElementById('users-tbody');
      tbody.innerHTML = '';
      Object.entries(data).forEach(([uname, u]) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td style="font-family:var(--mono);font-size:12px">${esc(uname)}</td>
          <td>${esc(u.display_name)}</td>
          <td><span class="mode-pill ${u.role === 'admin' ? 'active' : ''}"
                    style="cursor:default;font-size:11px">${u.role}</span></td>
          <td><span class="color-swatch" style="background:${u.box_color}" title="${u.box_color}"></span></td>
          <td class="flex gap-8">
            <button class="btn btn-ghost btn-sm" onclick="G.showEditUserModal('${esc(uname)}','${esc(u.display_name)}','${u.box_color}')">Edit</button>
            ${uname !== 'admin' ? `<button class="btn btn-danger btn-sm" onclick="G.deleteUser('${esc(uname)}')">Delete</button>` : ''}
          </td>`;
        tbody.appendChild(tr);
      });
    } catch(e) { console.error(e); }
  }

  function showAddUserModal() {
    document.getElementById('new-uname').value = '';
    document.getElementById('new-upass').value = '';
    document.getElementById('new-dname').value = '';
    document.getElementById('new-color').value = '#1565c0';
    document.getElementById('new-role').value = 'user';
    document.getElementById('add-user-error').classList.add('hidden');
    show('modal-add-user');
  }

  async function addUser() {
    const un = document.getElementById('new-uname').value.trim();
    const pw = document.getElementById('new-upass').value;
    const dn = document.getElementById('new-dname').value.trim();
    const color = document.getElementById('new-color').value;
    const role = document.getElementById('new-role').value;
    const errEl = document.getElementById('add-user-error');
    if (!un || !pw) { showError(errEl, 'Username and password required.'); return; }
    try {
      await post('/api/users/add', { username: un, password: pw,
        display_name: dn || un, box_color: color, role });
      closeModal('modal-add-user');
      loadUsers();
    } catch(e) { showError(errEl, e.detail || 'Failed to add user.'); }
  }

  function showEditUserModal(uname, dname, color) {
    document.getElementById('edit-uname-hidden').value = uname;
    document.getElementById('edit-user-title').textContent = `Edit: ${uname}`;
    document.getElementById('edit-dname').value = dname;
    document.getElementById('edit-pass').value = '';
    document.getElementById('edit-color').value = color;
    document.getElementById('edit-user-error').classList.add('hidden');
    show('modal-edit-user');
  }

  async function updateUser() {
    const un = document.getElementById('edit-uname-hidden').value;
    const dn = document.getElementById('edit-dname').value.trim();
    const pw = document.getElementById('edit-pass').value;
    const color = document.getElementById('edit-color').value;
    const errEl = document.getElementById('edit-user-error');
    const payload = { username: un, display_name: dn, box_color: color };
    if (pw) payload.new_password = pw;
    try {
      await post('/api/users/update', payload);
      closeModal('modal-edit-user');
      loadUsers();
    } catch(e) { showError(errEl, e.detail || 'Failed to update user.'); }
  }

  async function deleteUser(uname) {
    if (!confirm(`Delete user "${uname}"? This cannot be undone.`)) return;
    try {
      await post('/api/users/delete', { username: uname });
      loadUsers();
    } catch(e) { alert(e.detail || 'Failed to delete user.'); }
  }

  // ── Admin: Email Settings ───────────────────────────────
  async function loadEmailSettings() {
    try {
      const cfg = await get('/api/config');
      _cfg = cfg;
      document.getElementById('email-sender').value = cfg.email_sender || '';
      document.getElementById('email-pass').value = '';  // never pre-fill password
      document.getElementById('email-recipients').value = (cfg.email_recipients || []).join(', ');
      document.getElementById('email-cooldown').value = cfg.email_cooldown || 60;
    } catch(e) { console.error(e); }
  }

  async function saveEmailSettings() {
    const sender = document.getElementById('email-sender').value.trim();
    const pass = document.getElementById('email-pass').value;
    const recipRaw = document.getElementById('email-recipients').value;
    const recipients = recipRaw.split(',').map(s => s.trim()).filter(Boolean);
    const cooldown = parseInt(document.getElementById('email-cooldown').value);
    const statusEl = document.getElementById('email-status');
    const payload = { email_sender: sender, email_recipients: recipients, email_cooldown: cooldown };
    if (pass) payload.email_sender_pass = pass;
    try {
      await post('/api/config', payload);
      showMsg(statusEl, 'Saved.', true);
    } catch(e) { showMsg(statusEl, e.detail || 'Failed to save.', false); }
  }

  async function testEmail() {
    const statusEl = document.getElementById('email-status');
    showMsg(statusEl, 'Sending...', true);
    try {
      const res = await post('/api/email/test', {});
      showMsg(statusEl, res.ok ? 'Test email sent!' : `Failed: ${res.error}`, res.ok);
    } catch(e) { showMsg(statusEl, e.detail || 'Failed.', false); }
  }

  // ── Admin: System Settings ──────────────────────────────
  async function loadSystemSettings() {
    try {
      const cfg = await get('/api/config');
      _cfg = cfg;
      const thr = Math.round((cfg.detection_threshold || 0.3) * 100);
      document.getElementById('threshold-slider').value = thr;
      document.getElementById('threshold-val').textContent = (thr/100).toFixed(2);
      _privacyOn = cfg.privacy !== undefined ? cfg.privacy : true;
      const tog = document.getElementById('privacy-toggle');
      tog.className = 'toggle ' + (_privacyOn ? 'on' : '');
    } catch(e) { console.error(e); }
  }

  function togglePrivacy() {
    _privacyOn = !_privacyOn;
    document.getElementById('privacy-toggle').className = 'toggle ' + (_privacyOn ? 'on' : '');
  }

  async function saveSettings() {
    const thr = parseInt(document.getElementById('threshold-slider').value) / 100;
    const dangerLabel = document.getElementById('danger-label').value.trim() || undefined;
    const statusEl = document.getElementById('settings-status');
    try {
      await post('/api/config', { detection_threshold: thr, privacy: _privacyOn,
                                   ...(dangerLabel ? { danger_label: dangerLabel } : {}) });
      showMsg(statusEl, 'Settings saved.', true);
    } catch(e) { showMsg(statusEl, e.detail || 'Failed.', false); }
  }

  // ── Admin: Logs ─────────────────────────────────────────
  function loadLogs() {
    filterLogs();
  }

  function filterLogs() {
    const filter = (document.getElementById('log-filter')?.value || '').toLowerCase();
    const el = document.getElementById('admin-logs-text');
    if (!el) return;
    const filtered = _allLogs.filter(l => !filter || l.toLowerCase().includes(filter));
    el.textContent = filtered.join('\n');
    el.scrollTop = el.scrollHeight;
  }

  function exportLogs() {
    const content = _allLogs.join('\n');
    const blob = new Blob([content], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `garuda-logs-${new Date().toISOString().substring(0,10)}.txt`;
    a.click();
  }

  // ── Admin: Narada Commands ──────────────────────────────
  async function loadCommands() {
    try {
      const cfg = await get('/api/config');
      const cmds = cfg.custom_voice_commands || {};
      const tbody = document.getElementById('commands-tbody');
      tbody.innerHTML = '';
      if (Object.keys(cmds).length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="color:var(--text-3);text-align:center">No custom commands yet.</td></tr>';
        return;
      }
      Object.entries(cmds).forEach(([phrase, resp]) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td style="font-family:var(--mono);font-size:12px;color:var(--accent)">${esc(phrase)}</td>
          <td>${esc(resp)}</td>
          <td><button class="btn btn-danger btn-sm" onclick="G.deleteCommand('${esc(phrase)}')">Delete</button></td>`;
        tbody.appendChild(tr);
      });
    } catch(e) { console.error(e); }
  }

  function showAddCommandModal() {
    document.getElementById('cmd-phrase').value = '';
    document.getElementById('cmd-response').value = '';
    show('modal-add-command');
  }

  async function addCommand() {
    const phrase = document.getElementById('cmd-phrase').value.trim().toLowerCase();
    const response = document.getElementById('cmd-response').value.trim();
    if (!phrase || !response) { alert('Enter both phrase and response.'); return; }
    try {
      await post('/api/config/command/add', { phrase, response });
      closeModal('modal-add-command');
      loadCommands();
    } catch(e) { alert(e.detail || 'Failed.'); }
  }

  async function deleteCommand(phrase) {
    if (!confirm(`Delete command "${phrase}"?`)) return;
    try {
      await post('/api/config/command/delete', { phrase });
      loadCommands();
    } catch(e) { alert(e.detail || 'Failed.'); }
  }

  // ── Emergency Stop ──────────────────────────────────────
  async function emergencyStop() {
    if (!confirm('This will stop the entire Garuda system. Are you sure?')) return;
    await post('/api/emergency-stop', {});
  }

  // ── Forgot Password ─────────────────────────────────────
  function showForgotFlow() {
    const un = document.getElementById('login-username').value.trim();
    hide('login-form');
    show('forgot-form');
    document.getElementById('forgot-username').value = un;
    document.getElementById('forgot-otp-section').classList.add('hidden');
    document.getElementById('forgot-msg').classList.add('hidden');
  }

  async function sendForgotOTP() {
    const un = document.getElementById('forgot-username').value.trim();
    const msgEl = document.getElementById('forgot-msg');
    if (!un) { showMsg(msgEl, 'Enter your username.', false); return; }
    try {
      const res = await post('/api/forgot/send-otp', { username: un });
      if (res.bypass_otp) {
        showMsg(msgEl, `Email failed — Dev bypass OTP: ${res.bypass_otp}`, false);
      } else {
        showMsg(msgEl, 'OTP sent to the alert email.', true);
      }
      document.getElementById('forgot-otp-section').classList.remove('hidden');
    } catch(e) { showMsg(msgEl, e.detail || 'Failed.', false); }
  }

  async function resetPassword() {
    const otp = document.getElementById('forgot-otp').value.trim();
    const newpass = document.getElementById('forgot-newpass').value;
    const msgEl = document.getElementById('forgot-msg');
    if (!otp || !newpass) { showMsg(msgEl, 'Enter OTP and new password.', false); return; }
    try {
      await post('/api/forgot/reset', { otp, new_password: newpass });
      showMsg(msgEl, 'Password reset! You can now login.', true);
      setTimeout(() => showCards(), 2000);
    } catch(e) { showMsg(msgEl, e.detail || 'Invalid OTP.', false); }
  }

  // ── Admin Data Load ─────────────────────────────────────
  async function loadAdminData() {
    try {
      _cfg = await get('/api/config');
    } catch(e) {}
  }

  // ── Color Swatches ──────────────────────────────────────
  function buildColorSwatches() {
    const container = document.getElementById('color-swatches');
    if (!container) return;
    SWATCH_COLORS.forEach(c => {
      const s = document.createElement('span');
      s.className = 'color-swatch';
      s.style.background = c;
      s.onclick = () => { document.getElementById('new-color').value = c; };
      container.appendChild(s);
    });
  }

  // ── Utils ───────────────────────────────────────────────
  function show(id) { document.getElementById(id)?.classList.remove('hidden'); }
  function hide(id) { document.getElementById(id)?.classList.add('hidden'); }
  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }
  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }
  function showError(el, msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
  }
  function showMsg(el, msg, ok) {
    el.textContent = msg;
    el.className = 'status-msg ' + (ok ? 'ok' : 'err');
  }
  function closeModal(id) { document.getElementById(id)?.classList.add('hidden'); }

  async function get(url) {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw data;
    return data;
  }

  async function post(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw data;
    return data;
  }

  // ── Public API ─────────────────────────────────────────
  return {
    init, showCards, showLoginForm, submitLogin, verifyAdminOTP, logout,
    navigate, toggleMode,
    loadUsers, showAddUserModal, addUser, showEditUserModal, updateUser, deleteUser,
    loadEmailSettings, saveEmailSettings, testEmail,
    loadSystemSettings, togglePrivacy, saveSettings,
    loadLogs, filterLogs, exportLogs,
    loadCommands, showAddCommandModal, addCommand, deleteCommand,
    emergencyStop, showForgotFlow, sendForgotOTP, resetPassword,
    closeModal,
  };
})();

// Boot
document.addEventListener('DOMContentLoaded', G.init);

// Close modals on overlay click
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
  }
});

// Enter key on login
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const loginForm = document.getElementById('login-form');
    const otpForm   = document.getElementById('admin-otp-form');
    if (loginForm && !loginForm.classList.contains('hidden')) G.submitLogin();
    if (otpForm   && !otpForm.classList.contains('hidden'))   G.verifyAdminOTP();
  }
});

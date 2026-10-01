/* ============================================================
   Garuda — SPA logic
   ============================================================ */
// Tag the page with what it runs on, before anything paints (style.css,
// "Every browser, every device"). Apple's own browsers keep the full glass.
(function () {
  try {
    const ua = navigator.userAgent || '';
    const apple = /iPhone|iPad|iPod/.test(ua)
      || (/Macintosh/.test(ua) && /Safari\//.test(ua))            // Safari, Chrome and Edge on a Mac
      || (navigator.platform === 'MacIntel');
    const root = document.documentElement;
    if (!apple) root.classList.add('plat-other');
    const slowHint = (navigator.deviceMemory && navigator.deviceMemory <= 2)
      || (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2)
      || (window.matchMedia && window.matchMedia('(prefers-reduced-transparency: reduce)').matches);
    if (slowHint) root.classList.add('perf-lite');
  } catch (_) {}
})();

// Measure real frame times once the app is on screen; a device that cannot
// hold ~25 fps drops the blur and decoration for this visit. Apple devices
// are left alone (Low Power Mode halves their frame rate on purpose).
function _garudaProbeFrames() {
  const root = document.documentElement;
  if (!root.classList.contains('plat-other') || root.classList.contains('perf-lite')) return;
  if (document.hidden || !window.requestAnimationFrame) return;
  const gaps = [];
  let last = 0;
  function step(t) {
    if (document.hidden) return;                    // a hidden tab is throttled, not slow
    if (last) gaps.push(t - last);
    last = t;
    if (gaps.length < 90) { requestAnimationFrame(step); return; }
    gaps.sort((a, b) => a - b);
    if (gaps[gaps.length >> 1] > 40) root.classList.add('perf-lite');
  }
  requestAnimationFrame(step);
}

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
  let _allLogs      = [];
  let _presenceLogs = [];
  let _logsUnlocked = false;
  let _lastAlertState = false;
  let _uptimeBase = 0;          // seconds from backend
  let _uptimeReceivedAt = 0;    // Date.now() when received
  let _uptimeInterval = null;   // interval ID — cleared on logout to prevent accumulation
  let _wsRetryDelay = 3000; // WS reconnect backoff (resets on successful open)
  let _wsRetryTimer = null; // the one pending reconnect, if any
  // Garuda (home security) and Drishti (home automation) are one app; the
  // server tags the page with the product for the address it was opened on.
  const _PRODUCT = document.documentElement.dataset.product === 'security' ? 'security' : 'home';
  const _BRAND = _PRODUCT === 'security' ? 'GARUDA' : 'DRISHTI';
  const _HOME_PAGES = ['devices', 'auto', 'insights'];
  const _forProduct = items => _PRODUCT === 'security' ? items.filter(i => !_HOME_PAGES.includes(i.page)) : items;
  let _currentPage = 'dashboard';
  let _diAllclearTimer = null; // timer to auto-clear the "All Clear" DI state
  let _alarmInterval  = null; // setInterval ID for repeating alarm beep
  let _audioCtx       = null; // shared AudioContext — unlocked once during login user gesture
  let _wsAllowed = false;      // set true after login, false on logout to stop reconnect
  let _clipRecording = false;  // true while a server-side clip is being recorded

  // localStorage throws in private windows and when site data is blocked;
  // the app must still start there.
  function _lsGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
  function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }
  function _lsDel(k) { try { localStorage.removeItem(k); } catch (_) {} }

  function _fmtUptimeLive() {
    if (!_uptimeReceivedAt) return '—';
    const elapsed = Math.floor((Date.now() - _uptimeReceivedAt) / 1000);
    const total = _uptimeBase + elapsed;
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }

  // ── Toast notification system ──────────────────────────────
  function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    // The same message again updates the toast already showing ("... x2")
    // instead of stacking a copy on top of it.
    const same = [...container.children].find(t =>
      !t.classList.contains('removing') && t.dataset.msg === message && t.dataset.type === type);
    if (same) {
      const n = (+same.dataset.count || 1) + 1;
      same.dataset.count = n;
      same.querySelector('.toast-count').textContent = ` \u00d7${n}`;
      same.classList.remove('bump'); void same.offsetWidth; same.classList.add('bump');
      clearTimeout(same._timer);
      same._timer = setTimeout(() => _dismissToast(same), duration);
      return;
    }
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.dataset.msg = message;
    toast.dataset.type = type;
    const span = document.createElement('span');
    span.className = 'toast-msg';
    span.textContent = message;
    const count = document.createElement('span');
    count.className = 'toast-count';
    span.appendChild(count);
    const btn = document.createElement('button');
    btn.className = 'toast-dismiss';
    btn.innerHTML = '&times;';
    btn.onclick = () => _dismissToast(toast);
    toast.append(span, btn);
    container.appendChild(toast);
    if (container.children.length > 3) container.firstChild.remove();
    toast._timer = setTimeout(() => _dismissToast(toast), duration);
  }

  function _dismissToast(toast) {
    if (!toast.parentElement || toast.classList.contains('removing')) return;
    clearTimeout(toast._timer);
    toast.classList.remove('bump');
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 200);
  }

  // ── Hardware stats ────────────────────────────────────────
  function _updateHw(s) {
    if (s.cpu_percent != null) setText('hwm-cpu',  Math.round(s.cpu_percent) + '%');
    if (s.ram_percent != null) setText('hwm-ram',  Math.round(s.ram_percent) + '%');
    if (s.cpu_temp    != null) setText('hwm-temp', Math.round(s.cpu_temp) + '\u00b0');
    if (s.inference_fps != null) setText('hwm-fps', Math.round(s.inference_fps));
    if (s.disk_percent != null) setText('hwm-disk', Math.round(s.disk_percent) + '%');

    // Update metric rings (kept for any legacy consumers)
    if (s.cpu_percent != null) updateMetricRing('hw-cpu', s.cpu_percent, 100, '%');
    if (s.ram_percent != null) updateMetricRing('hw-ram', s.ram_percent, 100, '%');
    if (s.cpu_temp != null) updateMetricRing('hw-temp', s.cpu_temp, 85, '\u00b0C');
    if (s.inference_fps != null) updateMetricRing('hw-fps', s.inference_fps, 60, 'fps');
    if (s.disk_percent != null) updateMetricRing('hw-disk', s.disk_percent, 100, '%');

    // Update horizontal health bars
    _updateBar('hub-bar-cpu',  s.cpu_percent,   100);
    _updateBar('hub-bar-ram',  s.ram_percent,   100);
    _updateBar('hub-bar-temp', s.cpu_temp,       85);
    _updateBar('hub-bar-fps',  s.inference_fps,  60);
    _updateBar('hub-bar-disk', s.disk_percent,  100);
  }

  function _updateBar(id, value, max) {
    const el = document.getElementById(id);
    if (!el || value == null) return;
    const pct = Math.min(Math.max(value / max, 0), 1) * 100;
    el.style.width = pct.toFixed(1) + '%';
    el.classList.remove('warn', 'crit');
    if (pct > 90) el.classList.add('crit');
    else if (pct > 70) el.classList.add('warn');
  }

  // ── Hardware metric ring updater ───────────────────────────
  const RING_CIRCUMFERENCE = 2 * Math.PI * 27; // 169.65
  function updateMetricRing(id, value, max, unit) {
    const ring = document.getElementById(id + '-ring');
    if (!ring) return;
    const pct = Math.min(value / max, 1);
    const offset = RING_CIRCUMFERENCE * (1 - pct);
    ring.style.strokeDashoffset = offset;
    // Color: green < 60%, yellow 60-80%, red > 80%
    const color = pct < 0.6 ? '#30D158' : pct < 0.8 ? '#FF9F0A' : '#FF453A';
    ring.style.stroke = color;
  }

  // ── Activity feed ──────────────────────────────────────────
  let _activityItems = [];
  function _updateActivityFeed(d) {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;

    // Check for new system log entries
    const sysLog = d.system_log || [];
    const lastEntry = sysLog.length > 0 ? sysLog[sysLog.length - 1] : null;
    if (lastEntry && (!_activityItems.length || _activityItems[0].text !== lastEntry)) {
      // Determine type from log content
      let type = 'info';
      let text = lastEntry.replace(/^\[[\d\-: ]+\]\s*/, ''); // strip timestamp
      if (text.includes('[TAMPER]') || text.includes('Alert triggered')) type = 'danger';
      else if (text.includes('[WATCH]') || text.includes('[PRESENCE]')) type = 'watch';
      else if (text.includes('[OWNER]')) type = 'presence';

      const time = lastEntry.match(/^\[([\d\-: ]+)\]/)?.[1] || '';
      _activityItems.unshift({ text, type, time: time.split(' ').pop() || '' });
      if (_activityItems.length > 30) _activityItems.pop();

      _renderActivityFeed(feed);
    }
  }

  function _renderActivityFeed(feed) {
    if (!_activityItems.length) {
      feed.innerHTML = '<div class="empty-state"><div class="empty-state-icon">\u25CB</div><span>No activity yet this session</span></div>';
      return;
    }
    feed.innerHTML = _activityItems.map(item =>
      `<div class="activity-item">
        <div class="activity-dot ${esc(item.type)}"></div>
        <div class="activity-text">${esc(item.text)}</div>
        <div class="activity-time">${esc(item.time)}</div>
      </div>`
    ).join('');
  }

  const SWATCH_COLORS = [
    '#2997ff','#34c759','#ff3b30','#ff9f0a',
    '#af52de','#5e5ce6','#00c7be','#ff375f','#636366'
  ];

  const MODE_CFG = [
    { key:'privacy',   label:'Privacy Blur',     icon:'<span class="gi gi-privacy"></span>', cls:'mode-blue'   },
    { key:'night',     label:'Night Mode',        icon:'<span class="gi gi-night-mode"></span>', cls:'mode-purple' },
    { key:'dnd',       label:'Do Not Disturb',    icon:'<span class="gi gi-dnd"></span>', cls:'mode-warn'   },
    { key:'idle',      label:'Idle',              icon:'<span class="gi gi-idle"></span>', cls:'mode-muted'  },
    { key:'email_off', label:'Email Alerts Off',  icon:'<span class="gi gi-email-off"></span>', cls:'mode-muted'  },
    { key:'emergency', label:'Emergency',         icon:'<span class="gi gi-emergency"></span>', cls:'mode-danger' },
  ];

  // ── Backend URL config ───────────────────────────────────
  // True when the page itself is served by the Pi (LAN address or one of the
  // tunnel hostnames): the backend is this origin and needs no configuring.
  function _servedByPi() {
    const h = location.hostname;
    return h === 'localhost' || h === '127.0.0.1'
        || h.startsWith('192.168.') || h.startsWith('10.')
        || /^172\.(1[6-9]|2\d|3[01])\./.test(h)
        || /(^|\.)veeramanikanta\.in$/.test(h);
  }

  // AbortSignal.timeout() is missing in browsers older than 2022; without
  // this the status check threw there and always read as "not connected".
  function _timeoutSignal(ms) {
    if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) return AbortSignal.timeout(ms);
    if (typeof AbortController === 'undefined') return undefined;
    const c = new AbortController();
    setTimeout(() => c.abort(), ms);
    return c.signal;
  }

  function getBackend() {
    const h = location.hostname;
    const isLocal = h === 'localhost' || h === '127.0.0.1'
                 || h.startsWith('192.168.') || h.startsWith('10.')
                 || /^172\.(1[6-9]|2\d|3[01])\./.test(h);
    if (isLocal) return '';
    // garuda., drishti. and api. are all served by the Pi through the one
    // Cloudflare tunnel, so the page's own origin is the backend.
    if (/(^|\.)veeramanikanta\.in$/.test(h)) return '';
    // The Vercel copy is only the static front end; it talks to the Pi's API.
    if (h.endsWith('.vercel.app')) return _lsGet('garuda_backend') || 'https://api.veeramanikanta.in';
    return _lsGet('garuda_backend') || '';
  }

  function openBackendConfig() {
    $('m-bk-url').value = _lsGet('garuda_backend') || '';
    $('m-bk-msg').classList.add('hidden');
    show('m-backend');
  }

  async function saveBackendConfig() {
    let url = ($('m-bk-url').value || '').trim().replace(/\/$/, '');
    if (!url) { showEl('m-bk-msg', 'Enter a backend URL.', false); return; }
    if (!/^https?:\/\//.test(url)) url = 'http://' + url;
    showEl('m-bk-msg', 'Testing connection…', true);
    try {
      const r = await fetch(url + '/api/health', { signal: _timeoutSignal(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _lsSet('garuda_backend', url);
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
    // No URL on a page the Pi serves means "this origin", not "no backend".
    const displayHost = url
      ? (() => { try { return new URL(url).hostname; } catch(_){ return url; } })()
      : (_servedByPi() ? location.hostname : 'No backend');
    const row = dot.closest('.backend-row');
    const cfgBtn = row && row.querySelector('button');
    if (cfgBtn) cfgBtn.classList.toggle('hidden', !url && _servedByPi());
    if (!url && !_servedByPi()) { lbl.textContent = displayHost; dot.className = 'bk-dot'; return; }
    lbl.textContent = displayHost + ' · checking…';
    dot.className = 'bk-dot';
    // The liveness endpoint: public, cheap, and it lists no accounts.
    const pingUrl = (url || '') + '/api/health';
    // One retry: the first request through a cold tunnel can time out.
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const r = await fetch(pingUrl, { method:'GET', credentials:'omit', cache:'no-store', signal: _timeoutSignal(8000) });
        if (r.ok) { dot.className = 'bk-dot ok'; lbl.textContent = displayHost; return; }
      } catch(_) {}
    }
    dot.className = 'bk-dot fail';
    lbl.textContent = displayHost + ' · unreachable';
  }

  // ── Boot ─────────────────────────────────────────────────
  function _applyBrand() {
    const name = _BRAND.charAt(0) + _BRAND.slice(1).toLowerCase();
    document.title = name;
    document.querySelectorAll('.wv-name, .header-brand, #hud-brand').forEach(el => { el.textContent = _BRAND; });
    const tag = document.querySelector('.wv-tagline');
    if (tag) tag.textContent = _PRODUCT === 'security' ? 'AI Security Intelligence Platform' : 'Home automation, built on Garuda';
  }

  async function init() {
    _applyBrand();
    // Theme: apply saved preference before rendering (light is HTML default)
    const savedTheme = _lsGet('garuda_theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    const tBtn = document.getElementById('theme-toggle-btn');
    if (tBtn) tBtn.classList.toggle('is-dark', savedTheme === 'dark');

    // Register service worker for PWA installability
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }

    buildSwatches('m-swatches');
    const backend = getBackend();
    updateBackendStatus(backend);
    const samePi = _servedByPi();
    if (backend) _token = _lsGet('garuda_token');

    // Try to restore session from previous visit (cookie / garuda_token).
    // On a page the Pi serves the session cookie is enough, so always ask.
    const canRestore = samePi || !!_token;
    if (canRestore) {
      try {
        const session = await api('GET', '/api/session');
        _session = session;
        afterLogin();
        return;
      } catch(e) {
        _lsDel('garuda_token');
        _token = null;
      }
    }

    // Only a copy hosted elsewhere with no address saved needs configuring.
    if (!backend && !samePi) openBackendConfig();
    showLoginView('lv-main');
    renderHeatmap({});  // render empty heatmap; real data arrives via WS after login
  }

  // ── Login view switcher ───────────────────────────────────
  // A front end on another site (the Vercel copy) cannot use the Pi's
  // cookies, so it keeps the tokens itself and sends them as headers.
  function _storeAuth(res) {
    if (!getBackend()) return;
    if (res.token) { _token = res.token; _lsSet('garuda_token', _token); }
    if (res.refresh_token) _lsSet('garuda_refresh', res.refresh_token);
  }

  function showLoginView(viewId) {
    ['lv-main','lv-admin-1','lv-admin-2','lv-forgot','lv-masterkey'].forEach(id => {
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
      const r = await api('POST', '/api/admin/send-otp', { username: un, password: pw });
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
      _storeAuth(res);
      _pendingAdmin = null;
      afterLogin();
    } catch(e) {
      showLoginErr(errEl, extractError(e));
    }
  }

  function goMasterKey() {
    showLoginView('lv-masterkey');
    setTimeout(() => $('mk-login-key')?.focus(), 50);
  }

  async function submitMasterKeyLogin() {
    const key = ($('mk-login-key')?.value || '').trim();
    const errEl = $('mk-login-err');
    if (!key) { showLoginErr(errEl, 'Enter your master key.'); return; }
    try {
      const res = await api('POST', '/api/master_key/login', { key });
      _session = res;
      _storeAuth(res);
      afterLogin();
    } catch(e) {
      showLoginErr(errEl, extractError(e));
    }
  }

  async function submitLogin() {
    const un = ($('li-user')?.value || '').trim();
    const pw = $('li-pass')?.value || '';
    const remember = !!$('li-remember')?.checked;
    const errEl = $('li-err');
    errEl?.classList.add('hidden');
    if (!un || !pw) { showLoginErr(errEl, 'Enter username and password.'); return; }
    try {
      const res = await api('POST', '/api/login', { username: un, password: pw, remember_me: remember });
      _session = res;
      _storeAuth(res);
      // remember_me → 7-day refresh token issued server-side via httpOnly cookie
      afterLogin();
    } catch(e) {
      showLoginErr(errEl, extractError(e));
    }
  }

  function afterLogin() {
    $('app').classList.add('logged-in');
    $('hdr-user').textContent = _session.display_name || _session.username;
    buildNav(_session.role);
    $('main')?.classList.add('dash-active');
    _setDILabel(_BRAND);
    nav('dashboard');
    // Live uptime ticker — save ID so it can be cleared on logout
    if (_uptimeInterval) clearInterval(_uptimeInterval);
    _uptimeInterval = setInterval(() => { if (_uptimeReceivedAt) setText('s-uptime', _fmtUptimeLive()); }, 1000);
    // Always reset console visibility first, then show for admin only
    const cw = $('dash-console-wrap');
    if (cw) {
      cw.classList.add('hidden');
      if (_session.role === 'admin') cw.classList.remove('hidden');
    }
    // Non-admin profiles get the house at a glance where the console would be.
    $('dash-home-glance')?.classList.toggle('hidden', _session.role === 'admin' || _PRODUCT === 'security');
    if (window.H) H.onLogin(_session);
    // Set logs unlock state from session (master key login sets this true)
    _logsUnlocked = !!_session.logs_unlocked;
    $('logs-gate')?.classList.add('hidden');
    // Activity date picker — default to all dates and wire change
    const datePicker = document.getElementById('activity-date');
    if (datePicker) {
      const today = new Date().toISOString().split('T')[0];
      datePicker.value = today;
      datePicker.max = today;
      datePicker.onchange = _renderTimeline;
    }
    _wsAllowed = true;
    connectWS();
    setTimeout(_garudaProbeFrames, 2500);   // after the first paint and state push have settled
    // Haptic feedback on Dynamic Island tap
    const hudEl = document.getElementById('top-hud');
    if (hudEl && !hudEl._hapticBound) {
      hudEl._hapticBound = true;
      hudEl.addEventListener('click', () => { if (navigator.vibrate) navigator.vibrate(8); });
    }
    // Request browser notification permission (needed for alert popups)
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
    // Unlock AudioContext now while we are inside a user-gesture call stack.
    // Browsers block audio created outside a user gesture; login click is the
    // earliest safe opportunity to pre-create and resume the context so that
    // _beep() can play unconditionally when an alert fires later.
    const _AC = window.AudioContext || window.webkitAudioContext;
    if (_AC && (!_audioCtx || _audioCtx.state === 'closed')) {
      try { _audioCtx = new _AC(); _audioCtx.resume().catch(() => {}); } catch(_) {}
    }
    // Notify feedback widget so it can inject admin Inbox tab
    if (G._afterLoginHook) G._afterLoginHook(_session);
    _syncFeedbackVisibility();
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    _lsSet('garuda_theme', next);
    // Sync both toggle buttons (HUD + login page)
    document.getElementById('theme-toggle-btn')?.classList.toggle('is-dark', next === 'dark');
    document.getElementById('login-theme-toggle')?.classList.toggle('is-dark', next === 'dark');
  }

  // Ask before doing something that cannot be taken back. Resolves true only
  // on the confirm button; Escape, the backdrop and Cancel all mean no.
  let _confirmDone = null;
  function confirmAction(opts) {
    const o = opts || {};
    const ov = $('m-confirm');
    if (!ov) return Promise.resolve(window.confirm(o.title || 'Are you sure?'));
    if (_confirmDone) _confirmDone(false);
    setText('m-confirm-title', o.title || 'Are you sure?');
    const body = $('m-confirm-body');
    body.textContent = o.body || '';
    body.classList.toggle('hidden', !o.body);
    const ok = $('m-confirm-ok');
    ok.textContent = o.confirmLabel || 'Confirm';
    ok.className = 'btn ' + (o.danger === false ? 'btn-primary' : 'btn-danger');
    setText('m-confirm-cancel', o.cancelLabel || 'Cancel');
    ov.classList.remove('hidden');
    setTimeout(() => $('m-confirm-cancel')?.focus(), 30);
    return new Promise(resolve => {
      _confirmDone = result => {
        _confirmDone = null;
        ov.classList.add('hidden');
        resolve(!!result);
      };
    });
  }
  function _confirmAnswer(result) { if (_confirmDone) _confirmDone(result); }

  async function logout() {
    const ok = await confirmAction({
      title: 'Sign out?',
      body: 'You will need to sign in again to see the camera and control the house.',
      confirmLabel: 'Sign out',
    });
    if (ok) await _doLogout();
  }

  let _loggingOut = false;
  async function _doLogout() {
    if (_loggingOut) return;
    _loggingOut = true;
    try { await _doLogoutInner(); } finally { _loggingOut = false; }
  }

  async function _doLogoutInner() {
    try { await api('POST', '/api/logout', {}); } catch(_) {}
    if (window.N) N.stopVoice();
    // Clear uptime interval before resetting state
    if (_uptimeInterval) { clearInterval(_uptimeInterval); _uptimeInterval = null; }
    _wsAllowed = false;   // prevent reconnect after logout
    _stopAlarm();
    if (G._fbOnLogout) G._fbOnLogout();   // clean up feedback inbox tab
    _session = null; _token = null; _logsUnlocked = false;
    _recentDets = []; _prevAlertActive = false; _lastAlertState = false; _lastDetInfo = '';
    _timelineSig = '';
    _lsDel('garuda_token');
    _lsDel('garuda_refresh');
    _lsDel('garuda_remember');
    if (_wsRetryTimer) { clearTimeout(_wsRetryTimer); _wsRetryTimer = null; }
    _tickPending = null;
    if (_ws) { const old = _ws; _ws = null; try { old.close(); } catch (_) {} }
    $('app').classList.remove('logged-in');
    _syncFeedbackVisibility();
    $('ios-nav')?.querySelectorAll('.ios-item').forEach(el => el.remove());
    // Stop all camera streams (WebRTC / WS / MJPEG)
    stopCameraStream();
    const camOv = $('camera-overlay');
    if (camOv) camOv.classList.add('hidden');
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
      await api('POST', '/api/forgot/send-otp', { username: un });
      showEl('fp-msg', 'OTP sent to alert email.', true);
      const fpBlock = $('fp-otp-block');
      if (fpBlock) { fpBlock.classList.remove('hidden'); fpBlock.style.display = 'flex'; }
      if ($('fp-btn')) {
        $('fp-btn').textContent = 'Reset Password';
        $('fp-btn').onclick = G.doReset;
      }
    } catch(e) { showEl('fp-msg', e.detail || 'Failed.', false); }
  }

  async function doReset() {
    const un  = ($('fp-user')?.value || '').trim();
    const otp = ($('fp-otp')?.value || '').trim();
    const pw  = $('fp-newpass')?.value || '';
    if (!un || !otp || !pw) { showEl('fp-msg', 'Enter OTP and new password.', false); return; }
    try {
      await api('POST', '/api/forgot/reset', { username: un, otp, new_password: pw });
      showEl('fp-msg', 'Password reset! You can now sign in.', true);
      setTimeout(() => showLoginView('lv-main'), 2000);
    } catch(e) { showEl('fp-msg', e.detail || 'Invalid OTP.', false); }
  }

  // ── Camera overlay ────────────────────────────────────────
  // Priority: WebRTC (H.264, lowest latency) → WS binary JPEG (CF Tunnel)
  //            → MJPEG (universal fallback)
  let _activePc = null;   // RTCPeerConnection when WebRTC is active
  let _wsStream = null;   // WebSocket when WS-JPEG stream is active

  function _camSetStatus(txt) {
    const el = $('cam-status-txt');
    if (el) el.textContent = txt;
    // Update connect button: green dot + "Live" when streaming, red + "Connect" otherwise
    const dot   = $('cam-live-dot');
    const label = $('cam-btn-label');
    const isLive = txt === 'Live';
    if (dot)   dot.className = 'cam-live-dot ' + (isLive ? 'cam-dot-live' : 'cam-dot-off');
    if (label) label.textContent = isLive ? 'Live' : 'Connect';
    // Show/hide snapshot + record buttons
    const snapBtn = $('cam-snapshot-btn');
    const recBtn  = $('cam-record-btn');
    if (snapBtn) snapBtn.style.display = isLive ? '' : 'none';
    if (recBtn)  recBtn.style.display  = isLive ? '' : 'none';
  }

  function stopCameraStream() {
    if (_mjpegRetry) { clearTimeout(_mjpegRetry); _mjpegRetry = null; }
    const camImg   = $('cam-img');
    const camVideo = $('cam-video');
    if (camImg)   { camImg.onload = null; camImg.onerror = null; camImg.src = ''; camImg.style.display = 'none'; }
    if (camVideo) { camVideo.srcObject = null; camVideo.style.display = 'none'; }
    if (_activePc) { _activePc.close(); _activePc = null; }
    if (_wsStream) { _wsStream.close(); _wsStream = null; }
  }

  async function startWebRTC() {
    const backend = getBackend() || '';
    const camVideo = $('cam-video');
    const camOffline = $('cam-offline');
    try {
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
      });
      _activePc = pc;
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.ontrack = (ev) => {
        if (camVideo && ev.streams[0]) {
          camVideo.srcObject = ev.streams[0];
          camVideo.style.display = 'block';
          if (camOffline) camOffline.style.display = 'none';
          _camSetStatus('Live · WebRTC');
        }
      };
      pc.onconnectionstatechange = () => {
        if (['failed','disconnected','closed'].includes(pc.connectionState)) {
          _camSetStatus('WebRTC lost — retrying WS…');
          pc.close(); _activePc = null;
          startWsStream();
        }
      };
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const token = _token || '';
      const resp = await fetch(backend + '/webrtc/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Garuda-Token': token } : {}) },
        body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
        credentials: 'include',
      });
      if (!resp.ok) throw new Error('WebRTC offer rejected: ' + resp.status);
      const answer = await resp.json();
      await pc.setRemoteDescription(new RTCSessionDescription(answer));
    } catch(e) {
      console.warn('WebRTC failed, falling back to WS stream:', e);
      if (_activePc) { _activePc.close(); _activePc = null; }
      startWsStream();
    }
  }

  function startWsStream() {
    const backend  = getBackend() || '';
    const camImg   = $('cam-img');
    const camOffline = $('cam-offline');
    const wsBase   = backend.replace(/^http/, 'ws');
    const token    = _token ? `?token=${encodeURIComponent(_token)}` : '';
    const ws       = new WebSocket(wsBase + '/ws/stream' + token);
    ws.binaryType  = 'arraybuffer';
    _wsStream      = ws;
    let connected  = false;
    ws.onopen = () => { _camSetStatus('Connecting…'); };
    ws.onmessage = (ev) => {
      if (!camImg) return;
      const blob = new Blob([ev.data], { type: 'image/jpeg' });
      const url  = URL.createObjectURL(blob);
      const old  = camImg.src;
      camImg.onload = () => {
        URL.revokeObjectURL(old);
        if (!connected) {
          connected = true;
          camImg.style.display = 'block';
          if (camOffline) camOffline.style.display = 'none';
          _camSetStatus('Live · WS');
        }
      };
      camImg.src = url;
    };
    ws.onerror = () => {
      _camSetStatus('WS stream error — falling back to MJPEG');
      ws.close();
    };
    ws.onclose = () => {
      if (_wsStream === ws) { _wsStream = null; startMjpeg(); }
    };
  }

  let _mjpegRetry = null;

  function startMjpeg() {
    const backend    = getBackend() || '';
    const camImg     = $('cam-img');
    const camOffline = $('cam-offline');
    if (!camImg) return;

    // Clear any pending retry timer
    if (_mjpegRetry) { clearTimeout(_mjpegRetry); _mjpegRetry = null; }

    _camSetStatus('Connecting…');

    const streamToken = _token ? `?token=${encodeURIComponent(_token)}` : '';
    const src = backend + '/stream' + streamToken;

    // Remove old handlers before reassigning
    camImg.onload  = null;
    camImg.onerror = null;

    camImg.onload = () => {
      camImg.style.display = 'block';
      if (camOffline) camOffline.style.display = 'none';
      _camSetStatus('Live');
    };
    camImg.onerror = () => {
      camImg.style.display = 'none';
      if (camOffline) camOffline.style.display = 'flex';
      _camSetStatus('Retrying…');
      // Auto-retry every 3 s if camera button still active
      const btn = $('cam-toggle-btn');
      if (btn && btn.classList.contains('active')) {
        _mjpegRetry = setTimeout(() => startMjpeg(), 3000);
      }
    };

    // Set src — browser streams MJPEG natively with no per-frame JS overhead
    camImg.src = src;
  }

  function toggleCamera() {
    const btn = $('cam-toggle-btn');
    const isActive = btn && btn.classList.contains('active');
    if (!isActive) {
      if (btn) btn.classList.add('active');
      switchCamTab('live');
      // MJPEG is the fastest and most reliable for Pi5 local streaming.
      // Browser handles frame decoding natively — no JS per-frame overhead.
      startMjpeg();
    } else {
      stopCameraStream();
      if (btn) btn.classList.remove('active');
      _camSetStatus('Offline');
      const camOffline = $('cam-offline');
      if (camOffline) camOffline.style.display = 'flex';
    }
  }

  async function takeSnapshot() {
    try {
      const backend = getBackend() || '';
      const tok = _token ? `?token=${encodeURIComponent(_token)}` : '';
      const url = backend + '/api/snapshot' + tok;
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) { showToast('No frame available.', 'error'); return; }
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      const cd = res.headers.get('Content-Disposition') || '';
      const m = cd.match(/filename="([^"]+)"/);
      a.download = m ? m[1] : 'garuda_snapshot.jpg';
      a.click();
      URL.revokeObjectURL(a.href);
      showToast('Snapshot saved.', 'success');
    } catch(e) { showToast('Snapshot failed.', 'error'); }
  }

  const _REC_ICON = {
    rec:  '<span class="gi gi-record" aria-hidden="true"></span>',
    stop: '<span class="gi gi-stop" aria-hidden="true"></span>',
  };

  async function toggleClip() {
    const btn = $('cam-record-btn');
    try {
      if (!_clipRecording) {
        await api('POST', '/api/clip/start');
        _clipRecording = true;
        if (btn) { btn.innerHTML = _REC_ICON.stop; btn.classList.add('recording'); }
        showToast('Recording started — auto-stops at 60 s.', 'success');
      } else {
        const r = await api('POST', '/api/clip/stop');
        _clipRecording = false;
        if (btn) { btn.innerHTML = _REC_ICON.rec; btn.classList.remove('recording'); }
        showToast('Clip saved: ' + ((r.path || '').split('/').pop() || 'done'), 'success');
      }
    } catch(e) { showToast('Clip error: ' + (e.detail || e.message || ''), 'error'); }
  }

  // The floor plan is inlined (not an <object>) so the theme can ink it.
  let _floorplanLoaded = false;
  function _loadFloorplan() {
    const box = $('floorplan-svg');
    if (!box || _floorplanLoaded) return;
    _floorplanLoaded = true;
    fetch('/static/floorplan.svg?v=2').then(r => r.ok ? r.text() : Promise.reject())
      .then(svg => { box.innerHTML = svg; })
      .catch(() => { _floorplanLoaded = false; });
  }

  function switchCamTab(tab) {
    const live      = $('cam-view-live');
    const floor     = $('cam-view-floor');
    const tabLive   = $('cam-tab-live');
    const tabFloor  = $('cam-tab-floor');
    if (tab === 'live') {
      live?.classList.remove('hidden');
      floor?.classList.add('hidden');
      tabLive?.classList.add('active');
      tabFloor?.classList.remove('active');
    } else {
      _loadFloorplan();
      floor?.classList.remove('hidden');
      live?.classList.add('hidden');
      tabFloor?.classList.add('active');
      tabLive?.classList.remove('active');
    }
  }

  // ── Chat ──────────────────────────────────────────────────
  // ── Alert activity heatmap (backend-stored, lifetime-persistent) ────────
  let _lastHeatmapKey = '';

  function renderHeatmap(activity) {
    activity = activity || {};
    const container = $('heatmap');
    if (!container) return;

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const CELL = 10, GAP = 3, COL_W = CELL + GAP;
    const NUM_WEEKS = 13;

    // Find start Sunday: go back to the Sunday that is ≤ (NUM_WEEKS-1)*7 days ago
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const gridStart = new Date(today);
    gridStart.setDate(today.getDate() - (NUM_WEEKS - 1) * 7 - today.getDay());

    // Build columns (each = one week, Sun→Sat)
    const cols = [];
    for (let w = 0; w < NUM_WEEKS; w++) {
      const col = [];
      for (let d = 0; d < 7; d++) {
        const date = new Date(gridStart);
        date.setDate(gridStart.getDate() + w * 7 + d);
        if (date > today) { col.push(null); continue; }
        const key = date.toISOString().slice(0, 10);
        const count = activity[key] || 0;
        const level = count >= 8 ? 4 : count >= 5 ? 3 : count >= 3 ? 2 : count >= 1 ? 1 : 0;
        col.push({ key, count, level });
      }
      cols.push(col);
    }

    // Month labels: emit when month changes (skip if < 2 cols from edge)
    const monthLabels = [];
    let lastMonth = -1;
    cols.forEach((col, wi) => {
      const first = col.find(c => c !== null);
      if (!first) return;
      const m = new Date(first.key + 'T00:00:00').getMonth();
      if (m !== lastMonth && wi > 0) {
        monthLabels.push({ col: wi, label: MONTHS[m] });
        lastMonth = m;
      } else if (wi === 0) {
        lastMonth = m;
      }
    });

    // Build DOM
    container.innerHTML = '';
    const outer = mk('div', 'hm-outer');

    // Left: day labels column
    const left = mk('div', 'hm-left');
    [['', false], ['Mon', true], ['', false], ['Wed', true], ['', false], ['Fri', true], ['', false]]
      .forEach(([txt, vis]) => {
        const lbl = mk('div', 'hm-day-label');
        if (vis) lbl.textContent = txt;
        left.appendChild(lbl);
      });

    // Right: month row + grid
    const right = mk('div', 'hm-right');

    const monthRow = mk('div', 'hm-month-row');
    monthLabels.forEach(({ col, label }) => {
      const span = mk('span', 'hm-month-lbl');
      span.textContent = label;
      span.style.left = (col * COL_W) + 'px';
      monthRow.appendChild(span);
    });

    const grid = mk('div', 'hm-grid');
    cols.forEach(col => {
      col.forEach(cell => {
        const el = mk('div', cell ? `hm-cell hm-${cell.level}` : 'hm-cell hm-empty');
        if (cell && cell.count > 0)
          el.title = `${cell.key}: ${cell.count} alert${cell.count !== 1 ? 's' : ''}`;
        grid.appendChild(el);
      });
    });

    right.appendChild(monthRow);
    right.appendChild(grid);
    outer.appendChild(left);
    outer.appendChild(right);
    container.appendChild(outer);
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

  // ── iOS Bottom Navigation ─────────────────────────────────
  // Hand-drawn icons (icons/*.svg via .gi).
  const _NAV_ICONS = {
    dashboard: `<span class="gi gi-home"></span>`,
    narada:    `<span class="gi gi-narada"></span>`,
    users:     `<span class="gi gi-users"></span>`,
    email:     `<span class="gi gi-mail"></span>`,
    settings:  `<span class="gi gi-settings"></span>`,
    logs:      `<span class="gi gi-logs"></span>`,
    commands:  `<span class="gi gi-commands"></span>`,
    emergency: `<span class="gi gi-stop"></span>`,
    devices:   `<span class="gi gi-devices"></span>`,
    auto:      `<span class="gi gi-automate"></span>`,
    insights:  `<span class="gi gi-insights"></span>`,
    more:      `<span class="gi gi-more"></span>`,
    feedback:  `<span class="gi gi-feedback"></span>`,
    theme:     `<span class="gi gi-light-mode"></span>`,
    signout:   `<span class="gi gi-power"></span>`,
  };

  const _USER_NAV = [
    { page: 'dashboard', label: 'Home',   icon: 'dashboard' },
    { page: 'devices',   label: 'Devices', icon: 'devices'  },
    { page: 'auto',      label: 'Automate', icon: 'auto'    },
    { page: 'insights',  label: 'Insights', icon: 'insights' },
    { page: 'narada',    label: 'Narada', icon: 'narada'    },
  ];

  const _ADMIN_NAV = [
    { page: 'dashboard',  label: 'Home',     icon: 'dashboard' },
    { page: 'devices',    label: 'Devices',  icon: 'devices'   },
    { page: 'auto',       label: 'Automate', icon: 'auto'      },
    { page: 'insights',   label: 'Insights', icon: 'insights'  },
    { page: 'narada',     label: 'Narada',   icon: 'narada'    },
    { page: 'a-email',    label: 'Email',    icon: 'email'     },
    { page: 'a-settings', label: 'System',   icon: 'settings'  },
    { page: 'a-logs',     label: 'Logs',     icon: 'logs'      },
    { page: 'a-cmds',     label: 'Commands', icon: 'commands'  },
    { page: null,         label: 'Stop',     icon: 'emergency', danger: true },
  ];

  function movePill(itemEl, instant) {
    const pill  = $('ios-pill');
    const navEl = $('ios-nav');
    if (!pill || !navEl || !itemEl) return;
    const nr  = navEl.getBoundingClientRect();
    const ir  = itemEl.getBoundingClientRect();
    const ovr = 8;
    // getBoundingClientRect() is viewport-relative; pill is positioned in the
    // nav's scrollable content area, so we must add scrollLeft to compensate.
    let tx = ir.left - nr.left + navEl.scrollLeft - ovr;
    let w  = ir.width + ovr * 2;
    // Keep the pill inside the bar: the first and last tabs sit near its
    // rounded ends, where the overhang used to poke out.
    const inset = 4, maxX = navEl.scrollWidth - inset;
    if (tx < inset) { w -= inset - tx; tx = inset; }
    if (tx + w > maxX) w = maxX - tx;
    if (instant) {
      pill.style.transition = 'none';
      pill.style.transform  = `translateX(${tx}px)`;
      pill.style.width      = w + 'px';
      pill.offsetHeight;          // force reflow so transition disables cleanly
      pill.style.transition = '';
    } else {
      pill.style.transform = `translateX(${tx}px)`;
      pill.style.width     = w + 'px';
    }
  }

  // Phone mode: five tabs, the rest in an iOS-style "More" sheet. Eleven
  // items squeezed into a scrolling strip were unreadable at 390 px.
  const _PHONE_MQ = window.matchMedia('(max-width: 768px)');
  const _PHONE_PRIMARY = _PRODUCT === 'security'
    ? ['dashboard', 'narada']
    : ['dashboard', 'devices', 'auto', 'narada'];
  let _navRole = null;

  function _isPhone() { return _PHONE_MQ.matches; }

  function _navButton(item) {
    const btn = document.createElement('button');
    btn.className = 'ios-item' + (item.danger ? ' ios-danger' : '');
    btn.innerHTML = `<span class="ios-icon">${_NAV_ICONS[item.icon]}</span><span class="ios-label">${item.label}</span>`;
    if (item.page) btn.dataset.page = item.page;
    btn.setAttribute('aria-label', item.label);
    return btn;
  }

  function buildNav(role) {
    _navRole = role;
    const items = _forProduct(role === 'admin' ? _ADMIN_NAV : _USER_NAV);
    const navEl = $('ios-nav');
    if (!navEl) return;
    navEl.querySelectorAll('.ios-item').forEach(el => el.remove());
    const phone = _isPhone();
    navEl.classList.toggle('ios-nav-phone', phone);
    const shown = phone
      ? [...items.filter(i => _PHONE_PRIMARY.includes(i.page)),
         { page: 'more', label: 'More', icon: 'more' }]
      : items;
    shown.forEach(item => {
      const btn = _navButton(item);
      if (item.danger) btn.onclick = () => emergencyStop();
      else if (item.page === 'more') btn.onclick = () => openMoreSheet();
      else btn.onclick = () => nav(item.page, btn);
      navEl.appendChild(btn);
    });
    _buildMoreSheet(phone ? items.filter(i => !_PHONE_PRIMARY.includes(i.page)) : []);
    const current = _navItemFor(_currentPage) || navEl.querySelector('.ios-item:not(.ios-danger)');
    if (current) {
      current.classList.add('active');
      requestAnimationFrame(() => movePill(current, true));
    }
  }

  // The tab that represents a page: its own, or "More" on a phone.
  function _navItemFor(pageId) {
    if (!pageId) return null;
    return document.querySelector(`#ios-nav .ios-item[data-page="${pageId}"]`)
      || (_isPhone() ? document.querySelector('#ios-nav .ios-item[data-page="more"]') : null);
  }

  function _buildMoreSheet(items) {
    let sheet = $('more-sheet');
    if (!sheet) {
      sheet = document.createElement('div');
      sheet.id = 'more-sheet';
      sheet.className = 'more-sheet';
      sheet.setAttribute('aria-hidden', 'true');
      sheet.innerHTML = `<div class="more-backdrop"></div>
        <div class="more-panel" role="dialog" aria-label="More">
          <div class="more-grabber"></div>
          <div class="more-grid"></div>
          <div class="more-list"></div>
        </div>`;
      document.body.appendChild(sheet);
      sheet.querySelector('.more-backdrop').onclick = closeMoreSheet;
      _bindSheetDrag(sheet.querySelector('.more-panel'));
      document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMoreSheet(); });
    }
    const grid = sheet.querySelector('.more-grid');
    grid.innerHTML = '';
    items.filter(i => !i.danger).forEach(item => {
      const b = document.createElement('button');
      b.className = 'more-tile';
      b.dataset.page = item.page;
      b.innerHTML = `<span class="more-tile-icon">${_NAV_ICONS[item.icon]}</span><span>${item.label}</span>`;
      b.onclick = () => { closeMoreSheet(); nav(item.page); };
      grid.appendChild(b);
    });
    const list = sheet.querySelector('.more-list');
    list.innerHTML = '';
    const rows = [
      { icon: 'feedback', label: 'Send feedback', run: () => G.toggleFeedback && G.toggleFeedback() },
      { icon: 'theme', label: 'Switch appearance', run: toggleTheme },
      ...items.filter(i => i.danger).map(i => ({ icon: i.icon, label: 'Emergency stop', run: emergencyStop, danger: true })),
      { icon: 'signout', label: 'Sign out', run: logout, danger: true },
    ];
    rows.forEach(r => {
      const b = document.createElement('button');
      b.className = 'more-row' + (r.danger ? ' danger' : '');
      b.innerHTML = `<span class="more-row-icon">${_NAV_ICONS[r.icon]}</span><span>${r.label}</span>`;
      b.onclick = () => { closeMoreSheet(); setTimeout(r.run, 220); };
      list.appendChild(b);
    });
  }

  function openMoreSheet() {
    const sheet = $('more-sheet'); if (!sheet) return;
    sheet.querySelectorAll('.more-tile').forEach(t => t.classList.toggle('active', t.dataset.page === _currentPage));
    sheet.classList.add('open');
    sheet.setAttribute('aria-hidden', 'false');
    if (navigator.vibrate) navigator.vibrate(6);
  }

  function closeMoreSheet() {
    const sheet = $('more-sheet'); if (!sheet || !sheet.classList.contains('open')) return;
    sheet.classList.remove('open');
    sheet.setAttribute('aria-hidden', 'true');
    const panel = sheet.querySelector('.more-panel');
    panel.style.transform = '';
  }

  // Drag the sheet down to dismiss, like an iOS sheet.
  function _bindSheetDrag(panel) {
    let startY = null, dy = 0;
    panel.addEventListener('touchstart', e => { startY = e.touches[0].clientY; dy = 0; panel.style.transition = 'none'; }, { passive: true });
    panel.addEventListener('touchmove', e => {
      if (startY === null) return;
      dy = Math.max(0, e.touches[0].clientY - startY);
      panel.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    panel.addEventListener('touchend', () => {
      panel.style.transition = '';
      if (dy > 80) closeMoreSheet(); else panel.style.transform = '';
      startY = null;
    });
  }

  _PHONE_MQ.addEventListener('change', () => { if (_navRole) buildNav(_navRole); });

  // ── Dynamic Island helpers ────────────────────────────────
  const _DI_LABELS = {
    'dashboard':   _BRAND,
    'narada':      'Narada',
    'devices':     'Devices',
    'auto':        'Automate',
    'insights':    'Insights',
    'a-email':     'Email',
    'a-settings':  'Settings',
    'a-logs':      'Logs',
    'a-cmds':      'Commands',
  };

  function _setDILabel(label) {
    const el = document.getElementById('hud-brand');
    if (el) el.textContent = label;
  }

  function _syncDIContext() {
    const hud = document.getElementById('top-hud');
    const hudLabel = document.getElementById('hud-label');
    if (!hud) return;
    const thinking  = hud.classList.contains('di-thinking');
    const alerting  = hud.classList.contains('di-alert');
    const allclear  = hud.classList.contains('di-allclear');
    const yellow    = hud.classList.contains('di-yellow');
    // The voice pill while a Narada voice conversation is live.
    const voice = !!(window.N && N.voiceActive())
      && !thinking && !alerting && !allclear && !yellow;
    hud.classList.toggle('di-voice', voice);
    hud.classList.toggle('di-idle', !thinking && !alerting && !voice && !allclear && !yellow);
    if (!hudLabel) return;
    if (alerting)       hudLabel.textContent = 'Alert';
    else if (thinking)  hudLabel.textContent = 'Thinking';
    else if (allclear)  hudLabel.textContent = 'All Clear';
    else if (yellow)    hudLabel.textContent = 'Night Presence';
    else if (voice)     hudLabel.textContent = 'Voice';
    else                hudLabel.textContent = '';
  }

  function _startAlarm() {
    if (_alarmInterval) return;
    function _beep() {
      try {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        if (!_audioCtx || _audioCtx.state === 'closed') {
          _audioCtx = new AC();
        }
        _audioCtx.resume().then(() => {
          const osc  = _audioCtx.createOscillator();
          const gain = _audioCtx.createGain();
          osc.connect(gain);
          gain.connect(_audioCtx.destination);
          osc.type = 'sine';
          osc.frequency.value = 880;
          gain.gain.setValueAtTime(0.25, _audioCtx.currentTime);
          gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + 0.35);
          osc.start(_audioCtx.currentTime);
          osc.stop(_audioCtx.currentTime + 0.35);
        }).catch(() => {});
      } catch(e) {}
    }
    _beep();
    _alarmInterval = setInterval(_beep, 1400);
  }

  function _stopAlarm() {
    if (_alarmInterval) { clearInterval(_alarmInterval); _alarmInterval = null; }
  }

  function _setDIState(state) {
    const hud = document.getElementById('top-hud');
    if (!hud) return;
    // Cancel any pending allclear auto-dismiss
    if (_diAllclearTimer) { clearTimeout(_diAllclearTimer); _diAllclearTimer = null; }
    hud.classList.toggle('di-alert',    state === 'alert');
    hud.classList.toggle('di-thinking', state === 'thinking');
    hud.classList.toggle('di-allclear', state === 'allclear');
    hud.classList.toggle('di-yellow',   state === 'yellow');
    // Auto-dismiss "All Clear" after 2.5s
    if (state === 'allclear') {
      _diAllclearTimer = setTimeout(() => { _diAllclearTimer = null; _setDIState(''); }, 2500);
    }
    _syncDIContext();
  }

  function _syncFeedbackVisibility() {
    const appEl = $('app');
    if (!appEl) return;
    // Hide when not logged in
    const shouldHide = !appEl.classList.contains('logged-in');
    appEl.classList.toggle('fb-hidden', shouldHide);
    // On Narada, push the button up above the input bar instead of hiding
    appEl.classList.toggle('page-narada', _currentPage === 'narada');
  }

  // ── Navigation ────────────────────────────────────────────
  function nav(pageId, navEl) {
    _currentPage = pageId;
    if (!navEl || !navEl.isConnected) navEl = _navItemFor(pageId);
    // Always hide logs gate when navigating (re-shows if a-logs and not unlocked)
    $('logs-gate')?.classList.add('hidden');
    document.querySelectorAll('.page').forEach(p => {
      if (p.classList.contains('active')) {
        p.style.opacity = '0';
        setTimeout(() => { p.classList.remove('active'); p.style.opacity = ''; }, 0);
      } else {
        p.classList.remove('active');
      }
    });
    document.querySelectorAll('.ios-item').forEach(n => n.classList.remove('active'));
    // Dashboard uses overflow:hidden on #main to avoid nav-bar gap
    const mainEl = $('main');
    if (mainEl) mainEl.classList.toggle('dash-active', pageId === 'dashboard');
    const pg = $('page-' + pageId);
    if (pg) {
      requestAnimationFrame(() => {
        pg.classList.add('active');
        pg.style.opacity = '0';
        requestAnimationFrame(() => { pg.style.opacity = '1'; });
      });
    }
    if (navEl) {
      navEl.classList.add('active');
      movePill(navEl);
      if (window.innerWidth <= 768) {
        navEl.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      }
    }
    // Dynamic Island: update label per page
    _setDILabel(_DI_LABELS[pageId] || _BRAND);
    if (window.DI) DI.onNav(pageId);
    _syncDIContext();
    _syncFeedbackVisibility();
    if (pageId === 'a-email')    loadEmailCfg();
    if (pageId === 'a-settings') { loadSysCfg(); if (window.H) H.loadAI(); }
    if (pageId === 'a-logs') {
      if (_logsUnlocked) {
        fetchAndRenderLogs();
      } else {
        $('logs-gate')?.classList.remove('hidden');
        setTimeout(() => $('lg-key')?.focus(), 80);
      }
    }
    if (pageId === 'a-cmds')     loadCmds();
    if (window.H) H.onNav(pageId);
    if (window.N) N.onNav(pageId);
  }

  // ── Mobile sidebar (no-ops — replaced by iOS nav) ─────────
  function toggleMenu() {}
  function closeMobileMenu() {}

  // ── WebSocket ─────────────────────────────────────────────
  function connectWS() {
    if (!_wsAllowed) return;   // don't reconnect after logout
    if (_wsRetryTimer) { clearTimeout(_wsRetryTimer); _wsRetryTimer = null; }
    if (_ws) { const old = _ws; _ws = null; try { old.close(); } catch (_) {} }
    const base = getBackend();
    const tok = _token || (base ? _lsGet('garuda_token') : null);
    let wsUrl;
    if (base) {
      wsUrl = base.replace(/^http/, 'ws').replace(/\/$/, '') + '/ws' + (tok ? `?token=${encodeURIComponent(tok)}` : '');
    } else {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      wsUrl = `${proto}://${location.host}/ws`;
    }
    let ws;
    try { ws = new WebSocket(wsUrl); } catch (_) { _scheduleWsRetry(); return; }
    _ws = ws;
    ws.onopen = () => { _wsRetryDelay = 3000; _syncPendingEvents(); };
    ws.onmessage = e => {
      let state;
      try { state = JSON.parse(e.data); } catch(err) { console.warn('[Garuda] WS parse error', err); return; }
      _queueTick(state);
    };
    ws.onerror = () => {};
    ws.onclose = ev => {
      // A socket this function replaced must not start a second retry loop:
      // each loop closed the other's socket, reconnecting forever.
      if (_ws !== ws) return;
      _ws = null;
      // 4001: the server no longer knows this session. Ask once; api() then
      // refreshes the token or signs out instead of retrying for ever.
      if (ev && ev.code === 4001) {
        api('GET', '/api/session').then(() => _scheduleWsRetry()).catch(() => {});
        return;
      }
      _scheduleWsRetry();
    };
  }

  function _scheduleWsRetry() {
    if (!_wsAllowed || _wsRetryTimer) return;
    const delay = _wsRetryDelay;
    _wsRetryDelay = Math.min(_wsRetryDelay * 1.5, 30000);
    _wsRetryTimer = setTimeout(() => { _wsRetryTimer = null; connectWS(); }, delay);
  }

  // State pushes are applied once per frame, and not at all while the tab is
  // hidden: a burst of detections used to redraw the whole dashboard for each.
  let _tickPending = null, _tickRaf = 0;
  function _queueTick(state) {
    _tickPending = state;
    if (document.hidden) {
      // Alerts must still ring and notify from a background tab.
      if (state && !!state.alert_active !== !!_lastAlertState) _flushTick();
      return;
    }
    if (!_tickRaf) _tickRaf = requestAnimationFrame(_flushTick);
  }
  function _flushTick() {
    _tickRaf = 0;
    const state = _tickPending;
    _tickPending = null;
    if (!state) return;
    try { tick(state); } catch (err) { console.warn('[Garuda] state render error', err); }
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && _tickPending && !_tickRaf) _tickRaf = requestAnimationFrame(_flushTick);
  });

  // ── Offline event sync on reconnect ─────────────────────
  async function _syncPendingEvents() {
    try {
      const res = await api('GET', '/api/events/pending');
      if (res.events && res.events.length > 0) {
        showToast(`Synced ${res.events.length} queued events`, 'info');
        // Inject into activity feed
        res.events.forEach(ev => {
          let type = 'info';
          if (ev.event_type === 'DANGER' || ev.event_type === 'TAMPER') type = 'danger';
          else if (ev.event_type === 'WATCH') type = 'watch';
          else if (ev.event_type === 'PRESENCE') type = 'presence';
          const time = ev.timestamp ? ev.timestamp.split('T').pop().split('.')[0] : '';
          const text = `[${ev.event_type}] ${ev.label || ''}${ev.info ? ' — ' + ev.info : ''}`;
          _activityItems.unshift({ text, type, time });
        });
        if (_activityItems.length > 50) _activityItems.length = 50;
        const feed = document.getElementById('activity-feed');
        if (feed) _renderActivityFeed(feed);
      }
    } catch(_) {}
  }

  // ── Activity Timeline ─────────────────────────────────────
  let _timelineItems = [];

  let _timelineSig = '';
  function _logSig(log) { return log.length + '|' + (log.length ? log[log.length - 1] : ''); }

  function _updateTimeline(s) {
    const log = s.system_log || [];
    const sig = _logSig(log);
    if (sig === _timelineSig) return;         // nothing new: leave the list alone
    _timelineSig = sig;
    _timelineItems = log.map(entry => {
      const timeMatch = entry.match(/^\[([\d\-: ]+)\]/);
      const time = timeMatch ? timeMatch[1].trim() : '';
      const text = entry.replace(/^\[[\d\-: ]+\]\s*/, '');
      let type = '';
      if (text.includes('[TAMPER]') || text.includes('Alert triggered')) type = 'danger';
      else if (text.includes('[WATCH]')) type = 'watch';
      else if (text.includes('[PRESENCE]') || text.includes('[OWNER]')) type = 'presence';
      else if (/\[MODE\]|mode (on|off)/i.test(text)) type = 'warn';
      return { time, text, type, dateKey: time.split(' ')[0] || '' };
    }).reverse(); // newest first
    _renderTimeline();
  }

  function _renderTimeline() {
    const el = $('activity-timeline');
    if (!el) return;
    const filter = ($('activity-date')?.value) || null;
    const items = filter
      ? _timelineItems.filter(i => i.dateKey === filter)
      : _timelineItems.slice(0, 50);
    if (!items.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">\u25CB</div><span>No activity for this date</span></div>';
      return;
    }
    el.innerHTML = items.map(item =>
      `<div class="timeline-item">
        <div class="tl-dot ${esc(item.type)}"></div>
        <div class="tl-body">
          <div class="tl-title">${esc(item.text)}</div>
          <div class="tl-time">${esc(item.time)}</div>
        </div>
      </div>`
    ).join('');
  }

  // Android Chrome refuses `new Notification()` on a page (it throws) and
  // some browsers have no Notification at all; either used to abort the rest
  // of the state push, so an alert never reached the screen there.
  function _notifyAlert(body) {
    try {
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
      const opts = { body, icon: '/static/icon-192.png', tag: 'garuda-alert' };
      if (navigator.serviceWorker && navigator.serviceWorker.ready) {
        navigator.serviceWorker.ready
          .then(reg => reg.showNotification('Garuda Alert', opts))
          .catch(() => { try { new Notification('Garuda Alert', opts); } catch (_) {} });
      } else {
        new Notification('Garuda Alert', opts);
      }
    } catch (_) {}
  }

  function tick(s) {
    if (window.DI) DI.onState(s);
    if (!s || typeof s !== 'object') return;
    const pipeDot = $('pipeline-dot');
    const pipeLabel = $('pipeline-label');
    const hudDot = $('hud-dot');
    if (s.alert_active) {
      if (pipeDot) pipeDot.className = 'hdr-dot alert';
      if (pipeLabel) pipeLabel.textContent = 'Alert';
      if (hudDot) hudDot.className = 'hdr-dot alert';
      _setDIState('alert');
    } else if (s.night_presence_alert) {
      if (pipeDot) pipeDot.className = 'hdr-dot alert';
      if (pipeLabel) pipeLabel.textContent = 'Night Presence';
      if (hudDot) hudDot.className = 'hdr-dot alert';
      _setDIState('yellow');
    } else {
      if (pipeDot) pipeDot.className = 'hdr-dot online';
      if (pipeLabel) pipeLabel.textContent = 'Online';
      if (hudDot) hudDot.className = 'hdr-dot online';
      if (_prevAlertActive) {
        // Alert just cleared — show "All Clear" briefly in DI
        _lastDetInfo = '';
        _setDIState('allclear');
      } else {
        const hud = document.getElementById('top-hud');
        if (hud && !hud.classList.contains('di-thinking') && !hud.classList.contains('di-allclear') && !hud.classList.contains('di-yellow')) {
          _setDIState('');
        }
      }
    }

    // Push notification + alarm on new alert
    if (s.alert_active && !_lastAlertState) {
      _notifyAlert(s.danger_info || 'Danger detected \u2014 check camera feed');
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
      _startAlarm();
      // On mobile, scroll status card into view so alert is visible
      if (window.innerWidth <= 1023) {
        const sc = $('status-card');
        if (sc) sc.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
    if (!s.alert_active && _lastAlertState) _stopAlarm();
    _lastAlertState = s.alert_active;
    _prevAlertActive = !!s.alert_active;

    // Sync clip recording state — reset button if server auto-stopped
    if (typeof s.clip_recording === 'boolean' && _clipRecording && !s.clip_recording) {
      _clipRecording = false;
      const recBtn = $('cam-record-btn');
      if (recBtn) { recBtn.innerHTML = _REC_ICON.rec; recBtn.classList.remove('recording'); }
    }

    // Modes
    renderModes(s.modes);

    // Security status card
    const card = $('status-card');
    if (card) {
      if (s.alert_active) {
        card.classList.add('alert');
        card.classList.add('alert-active');
        setText('status-label', 'ALERT');
        setText('status-desc', s.danger_info || 'Threat detected');
      } else {
        card.classList.remove('alert');
        card.classList.remove('alert-active');
        setText('status-label', 'ALL CLEAR');
        setText('status-desc', 'No threats detected');
      }
      setText('status-last', s.last_alert ? timeSince(new Date(s.last_alert)) : 'Never');
    }

    // Stats — live uptime ticker
    if (s.uptime_seconds != null) {
      _uptimeBase = s.uptime_seconds;
      _uptimeReceivedAt = Date.now();
    }
    setText('s-uptime', _fmtUptimeLive());

    setText('s-alert', s.last_alert ? timeSince(new Date(s.last_alert)) : 'None');
    setText('s-thr', s.detection_threshold ? s.detection_threshold.toFixed(2) : '—');
    setText('s-pipeline', s.alert_active ? 'Alert' : 'Active');

    // Hardware stats
    _updateHw(s);

    // Alert activity heatmap — re-render only when data changes
    if (s.alert_history) {
      const key = JSON.stringify(s.alert_history);
      if (key !== _lastHeatmapKey) {
        _lastHeatmapKey = key;
        renderHeatmap(s.alert_history);
      }
    }

    // Recent detections — only add entry when danger_info carries a scissors trigger
    if (s.danger_info) maybeAddDetection(s.danger_info);

    // Owner badge — show device name
    const badge = $('owner-badge');
    if (badge) {
      badge.classList.toggle('hidden', !s.owner_present);
      if (s.owner_present && s.owner_name) {
        const nameEl = $('owner-badge-name');
        if (nameEl) nameEl.textContent = s.owner_name;
      }
    }

    // System console — admin dashboard only
    if (_session && _session.role === 'admin') {
      const con = $('sys-console');
      const conSig = _logSig(s.system_log || []);
      if (con && con.dataset.sig !== conSig) {
        con.dataset.sig = conSig;
        const logText = (s.system_log || []).join('\n');
        const atBot = con.scrollTop + con.clientHeight >= con.scrollHeight - 8;
        con.textContent = logText;
        if (atBot) con.scrollTop = con.scrollHeight;
      }
    }

    if (window.H && s.home) H.onState(s.home);

    // Activity feed (legacy hidden element) + new timeline
    _updateActivityFeed(s);
    _updateTimeline(s);

    // Log badge counts
    setText('log-count-system', (s.system_log || []).length || 0);
    setText('log-count-detection', s.detection_log_count || 0);
    setText('log-count-presence', s.presence_log_count || 0);
    setText('log-count-voice', ((s.voice_log || []).length + (s.voice_responses || []).length) || 0);


    // Security health panel
    _updateSecHealth(s);

    // Sync system_log from WS state for any live-updating consumers
    // (actual admin logs page fetches via /api/logs which requires master key)
    _allLogs = s.system_log || [];
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

  // ── Security health panel ──────────────────────────────────
  function _updateSecHealth(s) {
    let issues = 0;

    // Watchdog
    _setHealthRow('sh-watchdog',
      s.watchdog_ok !== false,
      s.watchdog_ok === false ? 'Heartbeat lost — possible tampering' : 'Heartbeat active');
    if (s.watchdog_ok === false) issues++;

    // Camera
    _setHealthRow('sh-camera',
      !s.camera_blind,
      s.camera_blind ? 'Lens may be blocked or covered' : 'Feed normal');
    if (s.camera_blind) issues++;

    // Thermal
    const tempOk = !s.throttled;
    const tempWarn = s.cpu_temp >= 70 && !s.throttled;
    _setHealthRow('sh-temp',
      tempOk && !tempWarn ? true : (tempWarn ? 'warn' : false),
      s.throttled ? `Throttling at ${Math.round(s.cpu_temp || 0)}\u00b0C` :
      tempWarn ? `${Math.round(s.cpu_temp)}\u00b0C — approaching limit` : 'Temperature normal');
    if (s.throttled) issues++;

    // Network
    _setHealthRow('sh-network',
      s.net_connected !== false,
      s.net_connected === false ? 'No network interface up' :
      s.net_iface ? `Connected via ${s.net_iface}` : 'Connected');
    if (s.net_connected === false) issues++;

    // Disk
    const diskOk = !s.disk_percent || s.disk_percent < 85;
    const diskWarn = s.disk_percent >= 85 && s.disk_percent < 95;
    _setHealthRow('sh-disk',
      diskOk ? true : (diskWarn ? 'warn' : false),
      s.disk_percent != null ? `${s.disk_used_gb}/${s.disk_total_gb} GB (${Math.round(s.disk_percent)}%)` : 'OK');
    if (s.disk_percent >= 95) issues++;

    // Event queue
    const pending = s.pending_sync || 0;
    const queueOk = s.net_online !== false && pending === 0;
    const queueWarn = s.net_online !== false && pending > 0;
    _setHealthRow('sh-queue',
      queueOk ? true : (queueWarn ? 'warn' : false),
      s.net_online === false ? `Offline — ${pending} events queued locally` :
      pending > 0 ? `${pending} events pending sync` : 'Online — no pending events');
    if (s.net_online === false) issues++;

    // Badge
    const badge = document.getElementById('sec-health-badge');
    if (badge) {
      badge.textContent = issues === 0 ? 'All OK' : `${issues} Issue${issues > 1 ? 's' : ''}`;
      badge.className = 'sec-health-badge' + (issues === 0 ? '' : issues >= 2 ? ' critical' : ' warn');
    }
  }

  function _setHealthRow(id, status, desc) {
    const row = document.getElementById(id);
    if (!row) return;
    const icon = row.querySelector('.sec-health-icon');
    const descEl = row.querySelector('.sec-health-desc');
    const mark = String(status);
    if (icon && icon.dataset.s !== mark) {
      icon.dataset.s = mark;
      if (status === true) {
        icon.className = 'sec-health-icon ok';
        icon.innerHTML = '&#10003;';
      } else if (status === 'warn') {
        icon.className = 'sec-health-icon warn';
        icon.innerHTML = '!';
      } else {
        icon.className = 'sec-health-icon critical';
        icon.innerHTML = '&#10007;';
      }
    }
    if (descEl && descEl.textContent !== desc) descEl.textContent = desc;
  }

  // ── Narada conversation feed ──────────────────────────────
  function renderModes(modes) {
    modes = modes || {};
    const grid   = $('modes-pills');
    const hpills = $('header-pills');
    if (!grid) return;

    // Update rows in-place (preserves CSS transitions); create if first render
    MODE_CFG.forEach(m => {
      const isOn = !!modes[m.key];
      let row = grid.querySelector(`[data-mode="${m.key}"]`);
      if (!row) {
        row = mk('div', `mode-row ${m.cls}`);
        row.dataset.mode = m.key;
        row.innerHTML = `
          <div class="mode-row-icon">${m.icon}</div>
          <span class="mode-row-label">${m.label}</span>
          <div class="mode-toggle"></div>`;
        row.addEventListener('click', () => {
          const currentOn = row.classList.contains('on');
          toggleMode(m.key, currentOn);
        });
        grid.appendChild(row);
      }
      // A row the user just tapped keeps what they chose until the server
      // answers; a push sent before then would flick the switch back.
      if (row.dataset.pending === '1') return;
      // Smooth in-place state update (CSS transitions play)
      row.classList.toggle('on', isOn);
      const toggle = row.querySelector('.mode-toggle');
      if (toggle) toggle.classList.toggle('on', isOn);
    });

    // Header pills — only active modes
    const pillKey = MODE_CFG.filter(m => modes[m.key]).map(m => m.key).join(',');
    if (hpills && hpills.dataset.key !== pillKey) {
      hpills.dataset.key = pillKey;
      hpills.innerHTML = '';
      MODE_CFG.filter(m => modes[m.key]).forEach(m => {
        const p = mk('span', 'mode-pill active');
        p.textContent = m.label;
        hpills.appendChild(p);
      });
    }
  }

  async function toggleMode(mode, currentOn) {
    const row = $('modes-pills')?.querySelector(`[data-mode="${mode}"]`);
    // Prevent double-click mid-request
    if (row?.dataset.pending === '1') return;
    if (row) row.dataset.pending = '1';

    // Optimistic update — flip immediately without waiting for WS tick
    if (row) {
      row.classList.toggle('on', !currentOn);
      const toggle = row.querySelector('.mode-toggle');
      if (toggle) toggle.classList.toggle('on', !currentOn);
    }
    try {
      await api('POST', '/api/modes', { mode, value: !currentOn });
    } catch(e) {
      // Revert on failure
      if (row) {
        row.classList.toggle('on', currentOn);
        const toggle = row.querySelector('.mode-toggle');
        if (toggle) toggle.classList.toggle('on', currentOn);
      }
      showToast(extractError(e), 'error');
    } finally {
      if (row) delete row.dataset.pending;
    }
  }

  // ── Admin: Email ──────────────────────────────────────────
  async function loadEmailCfg() {
    try {
      const cfg = await api('GET', '/api/config');
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
    try { await api('POST', '/api/config', payload); showToast('Email settings saved.', 'success'); }
    catch(e) { showToast(e.detail || 'Failed to save email settings.', 'error'); }
  }

  async function testEmail() {
    showToast('Sending test email\u2026', 'info', 3000);
    try {
      const r = await api('POST', '/api/email/test', {});
      showToast(r.ok ? 'Test email sent!' : 'Failed: ' + r.error, r.ok ? 'success' : 'error');
    } catch(e) { showToast(e.detail || 'Failed to send test email.', 'error'); }
  }

  let _npOn = true;   // night presence alarm enabled flag

  // ── Admin: System settings ────────────────────────────────
  async function loadSysCfg() {
    try {
      const cfg = await api('GET', '/api/config');
      const t = Math.round((cfg.detection_threshold || 0.3) * 100);
      $('thr-slider').value = t;
      $('thr-val').textContent = (t / 100).toFixed(2);
      _privacyOn = cfg.privacy !== undefined ? cfg.privacy : true;
      $('priv-toggle').className = 'toggle' + (_privacyOn ? ' on' : '');
      const wl = $('watch-labels');
      if (wl) wl.value = (cfg.watch_labels || []).join(', ');
      const dl = $('danger-lbl');
      if (dl) dl.value = (cfg.danger_labels || []).join(', ');
      // Scheduled modes
      const sched = cfg.mode_schedule || {};
      _loadSchedField('night', sched.night);
      _loadSchedField('dnd',   sched.dnd);
      _loadSchedField('idle',  sched.idle);
      // Night presence window
      const npw = cfg.night_presence_window || {};
      _npOn = npw.enabled !== false;
      const npTog = $('np-toggle');
      if (npTog) npTog.className = 'toggle' + (_npOn ? ' on' : '');
      const nps = $('np-start'), npe = $('np-end');
      if (nps) nps.value = npw.start || '01:30';
      if (npe) npe.value = npw.end   || '05:00';
    } catch(e) {}
    loadDevices();
    loadMasterKeys();
  }

  function toggleNightPresence() {
    _npOn = !_npOn;
    const el = $('np-toggle');
    if (el) el.className = 'toggle' + (_npOn ? ' on' : '');
  }

  function _loadSchedField(mode, sched) {
    const s = $('sched-' + mode + '-start');
    const e = $('sched-' + mode + '-end');
    if (s) s.value = (sched && sched.start) || '';
    if (e) e.value = (sched && sched.end)   || '';
  }

  function _buildSchedule() {
    const modes = ['night', 'dnd', 'idle'];
    const out = {};
    for (const m of modes) {
      const s = ($('sched-' + m + '-start') || {}).value || '';
      const e = ($('sched-' + m + '-end')   || {}).value || '';
      if (s && e) out[m] = { start: s, end: e };
    }
    return out;
  }

  function togglePrivacy() {
    _privacyOn = !_privacyOn;
    $('priv-toggle').className = 'toggle' + (_privacyOn ? ' on' : '');
  }

  async function saveSettings() {
    const thr = parseInt($('thr-slider').value) / 100;
    const dlRaw = val('danger-lbl') || '';
    const dangerLabels = dlRaw.split(',').map(s => s.trim()).filter(Boolean);
    const wlRaw = val('watch-labels') || '';
    const watchLabels = wlRaw.split(',').map(s => s.trim()).filter(Boolean);
    try {
      const npStartVal = ($('np-start') || {}).value || '';
      const npEndVal   = ($('np-end')   || {}).value || '';
      const payload = {
        detection_threshold: thr,
        privacy: _privacyOn,
        watch_labels: watchLabels,
        ...(dangerLabels.length > 0 ? { danger_labels: dangerLabels } : {}),
        night_presence_enabled: _npOn,
        ...(npStartVal ? { night_presence_start: npStartVal } : {}),
        ...(npEndVal   ? { night_presence_end:   npEndVal   } : {}),
      };
      const sched = _buildSchedule();
      if (Object.keys(sched).length > 0) payload.mode_schedule = sched;
      await api('POST', '/api/config', payload);
      showToast('Settings saved.', 'success');
    } catch(e) { showToast(e.detail || 'Failed to save settings.', 'error'); }
  }

  // ── Admin: Logs ───────────────────────────────────────────
  async function unlockLogs() {
    const key = ($('lg-key')?.value || '').trim();
    const errEl = $('lg-err');
    if (!key) { showEl('lg-err', 'Enter master key.', false); errEl?.classList.remove('hidden'); return; }
    try {
      await api('POST', '/api/master_key/verify', { key });
      _logsUnlocked = true;
      $('logs-gate')?.classList.add('hidden');
      if ($('lg-key')) $('lg-key').value = '';
      fetchAndRenderLogs();
    } catch(e) {
      if (errEl) { errEl.textContent = extractError(e); errEl.classList.remove('hidden'); }
    }
  }

  async function fetchAndRenderLogs() {
    try {
      const data = await api('GET', '/api/logs');
      _allLogs = data.system_log || [];
      _presenceLogs = data.presence_log || [];
      renderLogs();
      const av = $('a-vlog');
      if (av) {
        av.innerHTML = [...(data.voice_log||[]), ...(data.voice_responses||[])]
          .map(l => `<div class="log-line">${esc(l)}</div>`).join('');
        av.scrollTop = av.scrollHeight;
      }
      const dl = $('a-detlog');
      if (dl) {
        const dets = data.detection_log || [];
        dl.textContent = dets.length ? dets.join('\n') : 'No detection events this session.';
        dl.scrollTop = dl.scrollHeight;
      }
    } catch(e) {
      // 403 means logs not unlocked — re-show gate
      if (e && (e.detail || '').toString().includes('Master key')) {
        _logsUnlocked = false;
        $('logs-gate')?.classList.remove('hidden');
      }
    }
  }

  async function downloadFullLog() {
    const base = getBackend();
    const tok  = _token || (base ? _lsGet('garuda_token') : null);
    const url  = (base ? base.replace(/\/$/, '') : '') + '/api/logs/download';
    const headers = {};
    if (tok) headers['X-Garuda-Token'] = tok;
    try {
      const r = await fetch(url, { method: 'GET', headers, credentials: base ? 'omit' : 'include' });
      if (!r.ok) { showToast('Download failed \u2014 make sure logs are unlocked.', 'error'); return; }
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `garuda-full-log-${new Date().toISOString().slice(0,10)}.txt`;
      a.click();
    } catch(e) {
      showToast('Download error: ' + (e.message || e), 'error');
    }
  }

  function renderLogs() {
    const q = (val('log-q') || '').toLowerCase();
    const el = $('a-syslog');
    if (el) {
      const lines = _allLogs.filter(l => !q || l.toLowerCase().includes(q));
      el.textContent = lines.join('\n');
      el.scrollTop = el.scrollHeight;
    }
    const pl = $('a-preslog');
    if (pl) {
      if (_presenceLogs.length) {
        pl.textContent = _presenceLogs.map(e => {
          const icon = e.event === 'arrived' ? '→' : '←';
          return `${e.ts}  ${icon}  ${e.device || 'Unknown'}  (${e.mac || 'no mac'})`;
        }).join('\n');
      } else {
        pl.textContent = 'No presence events yet.';
      }
      pl.scrollTop = pl.scrollHeight;
    }
  }

  function filterLogs() { if (_logsUnlocked) renderLogs(); }

  // ── Log tab switching ──────────────────────────────────────
  function switchLogTab(tab) {
    document.querySelectorAll('.log-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.log-pane').forEach(p => p.classList.remove('active'));
    const tabEl = document.querySelector(`.log-tab[data-log="${tab}"]`);
    const paneEl = document.getElementById(`log-pane-${tab}`);
    if (tabEl) tabEl.classList.add('active');
    if (paneEl) paneEl.classList.add('active');
  }

  // ── Docs tab switching ─────────────────────────────────────
  function switchDocsTab(btn, sectionId) {
    document.querySelectorAll('.docs-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.docs-section').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    const section = document.getElementById(sectionId);
    if (section) section.classList.add('active');
    const behavior = window.innerWidth <= 768 ? 'auto' : 'smooth';
    btn.scrollIntoView({ behavior, inline: 'center', block: 'nearest' });
    const body = btn.closest('.docs-body');
    if (body) body.scrollTo({ top: 0, behavior });
  }

  function exportLogs() {
    const blob = new Blob([_allLogs.join('\n')], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `garuda-logs-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
  }

  // ── Device management (owner presence) ───────────────────
  async function loadDevices() {
    const el = $('devices-list'); if (!el) return;
    try {
      const data = await api('GET', '/api/devices');
      const devs = data.devices || [];
      if (!devs.length) {
        el.innerHTML = '<div class="device-empty">No devices registered.</div>';
        return;
      }
      el.innerHTML = devs.map(d => `
        <div class="device-row">
          <span class="device-dot ${d.online ? 'online' : ''}"></span>
          <span class="device-name">${esc(d.name)}</span>
          <span class="device-mac">${esc(d.mac)}</span>
          <button class="btn btn-ghost btn-sm dev-del-btn" data-mac="${esc(d.mac)}">Remove</button>
        </div>`).join('');
      el.querySelectorAll('.dev-del-btn').forEach(btn => {
        btn.addEventListener('click', () => G.deleteDevice(btn.dataset.mac));
      });
    } catch(e) { el.innerHTML = '<div class="device-empty" style="color:var(--danger)">Failed to load devices.</div>'; }
  }

  async function addDevice() {
    const name = val('dev-name').trim();
    const mac  = val('dev-mac').trim().toLowerCase();
    const MAC_RE = /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/;
    if (!name) { showToast('Enter a device name.', 'error'); return; }
    if (!MAC_RE.test(mac)) { showToast('Invalid MAC format (e.g. a4:c3:f0:12:34:56).', 'error'); return; }
    try {
      await api('POST', '/api/devices/add', { name, mac });
      $('dev-name').value = ''; $('dev-mac').value = '';
      showToast('Device added.', 'success');
      loadDevices();
    } catch(e) { showToast(e.detail || 'Failed to add device.', 'error'); }
  }

  async function deleteDevice(mac) {
    if (!await confirmAction({ title: 'Remove this device?', body: `${mac} will no longer count as the owner being home.`, confirmLabel: 'Remove' })) return;
    try {
      await api('POST', '/api/devices/delete', { mac });
      loadDevices();
    } catch(e) { showToast(extractError(e), 'error'); }
  }

  async function scanNetwork() {
    const btn = $('scan-btn');
    const el  = $('network-scan-list');
    if (!el) return;
    if (btn) btn.textContent = 'Scanning…';
    el.style.display = 'flex';
    el.innerHTML = '<div class="device-empty">Scanning local network…</div>';
    try {
      const data = await api('GET', '/api/arp');
      const entries = data.entries || [];
      if (!entries.length) {
        el.innerHTML = '<div class="device-empty">No devices found. Try again in 30s after presence poller runs.</div>';
      } else {
        el.innerHTML = '<div class="device-empty" style="margin-bottom:4px;color:var(--t2)">Click a device to register it as the owner\'s phone:</div>'
          + entries.map(e => `
            <div class="device-row scan-entry" style="cursor:${e.registered?'default':'pointer'}"
              data-mac="${esc(e.mac)}" data-ip="${esc(e.ip)}" data-registered="${e.registered?'1':''}">
              <span class="device-dot ${e.registered ? 'online' : ''}"></span>
              <span class="device-mac" style="flex:1">${esc(e.mac)}</span>
              <span style="font-size:11px;color:var(--t3)">${esc(e.ip)}</span>
              ${e.registered ? '<span style="font-size:11px;color:var(--success)">registered</span>' : ''}
            </div>`).join('');
        el.querySelectorAll('.scan-entry').forEach(row => {
          if (!row.dataset.registered) {
            row.addEventListener('click', () => G._regFromScan(row.dataset.mac, row.dataset.ip));
          }
        });
      }
    } catch(e) {
      el.innerHTML = '<div class="device-empty" style="color:var(--danger)">Scan failed.</div>';
    }
    if (btn) btn.textContent = 'Scan Network';
  }

  function _regFromScan(mac, ip) {
    const nameEl = $('dev-name');
    const macEl  = $('dev-mac');
    if (nameEl) nameEl.value = `Device (${ip})`;
    if (macEl)  macEl.value  = mac;
    showToast('MAC pre-filled \u2014 enter a name and click Add.', 'info');
  }

  // ── Presence refresh ──────────────────────────────────────
  async function refreshPresence() {
    const btn = document.querySelector('.owner-refresh-btn');
    if (btn) { btn.textContent = '…'; btn.disabled = true; }
    try {
      await api('POST', '/api/presence_refresh', {});
    } catch(e) {}
    if (btn) { btn.textContent = '↻'; btn.disabled = false; }
  }

  // ── Master key strength checker ───────────────────────────
  const _MK_COMMON = ['password','master','admin','garuda','security','qwerty',
    'asdfgh','zxcvbn','123456','654321','abcdef','letmein','welcome','login',
    'access','camera','house','home','lock','safe'];

  function _mkStrength(key) {
    const rules = [
      { id:'len12',  label:'At least 12 characters',       pass: key.length >= 12,                       req: true  },
      { id:'len14',  label:'14+ characters (recommended)', pass: key.length >= 14,                       req: false },
      { id:'upper',  label:'Uppercase letter (A–Z)',        pass: /[A-Z]/.test(key),                      req: true  },
      { id:'lower',  label:'Lowercase letter (a–z)',        pass: /[a-z]/.test(key),                      req: true  },
      { id:'num',    label:'Number (0–9)',                  pass: /[0-9]/.test(key),                      req: true  },
      { id:'sym',    label:'Symbol  (!@#$%^&* etc.)',       pass: /[^A-Za-z0-9]/.test(key),               req: true  },
      { id:'noSeq',  label:'No keyboard sequences',        pass: !_MK_COMMON.some(s => key.toLowerCase().includes(s)), req: true },
      { id:'noRep',  label:'No long repeating characters', pass: !/(.)\1{3,}/.test(key),                 req: true  },
    ];
    const required = rules.filter(r => r.req);
    const passed   = rules.filter(r => r.pass);
    const score    = passed.length;
    let strength = '', color = '';
    if (key.length > 0) {
      if (score <= 3)      { strength = 'Very Weak'; color = '#FF3B30'; }
      else if (score <= 4) { strength = 'Weak';      color = '#FF9F0A'; }
      else if (score <= 5) { strength = 'Fair';      color = '#FFD60A'; }
      else if (score <= 6) { strength = 'Strong';    color = '#30D158'; }
      else                 { strength = 'Very Strong'; color = '#34C759'; }
    }
    const pct = key.length ? Math.round((score / rules.length) * 100) : 0;
    const allReqPassed = required.every(r => r.pass);
    return { rules, score, strength, color, pct, allReqPassed };
  }

  function onMkKeyInput() {
    const key = $('mk-new-in')?.value || '';
    const { rules, strength, color, pct } = _mkStrength(key);
    const fill = $('mk-strength-fill');
    const lbl  = $('mk-strength-label');
    const rulesEl = $('mk-rules');
    if (fill) { fill.style.width = pct + '%'; fill.style.background = color; }
    if (lbl)  { lbl.textContent = strength; lbl.style.color = color; }
    if (rulesEl) {
      rulesEl.innerHTML = rules.map(r => `
        <div class="mk-rule ${r.pass ? 'pass' : (r.req ? 'fail' : 'opt')}">
          <span class="mk-rule-icon">${r.pass ? '✓' : '–'}</span>
          <span>${r.label}${!r.req ? ' <em>(optional)</em>' : ''}</span>
        </div>`).join('');
    }
  }

  // ── Master Keys management ────────────────────────────────
  async function loadMasterKeys() {
    const el = $('mk-list'); if (!el) return;
    try {
      const data = await api('GET', '/api/master_keys');
      const keys = data.keys || [];
      if (!keys.length) {
        el.innerHTML = '<div style="font-size:12px;color:var(--t3)">No master keys found.</div>';
        return;
      }
      el.innerHTML = keys.map((k, i) => `
        <div class="mk-item">
          <span>${esc(k)}</span>
          ${keys.length > 1 ? `<button class="mk-item-del" onclick="G.deleteMasterKey(${i})" title="Delete">×</button>` : ''}
        </div>`).join('');
    } catch(e) {
      el.innerHTML = '<div style="font-size:12px;color:var(--t3)">Could not load keys.</div>';
    }
  }

  async function requestMkOtp() {
    const current = ($('mk-current')?.value || '').trim();
    if (!current) { showToast('Enter your current master key first.', 'error'); return; }
    try {
      const r = await api('POST', '/api/master_key/request_otp', { current_key: current });
      const row = $('mk-otp-row');
      if (row) { row.classList.remove('hidden'); row.style.display = 'flex'; }
      if (r.bypass_otp && location.hostname === 'localhost') {
        showToast('Email failed. Dev OTP: ' + r.bypass_otp, 'error', 8000);
      } else {
        showToast('OTP sent to your alert email.', 'success');
      }
    } catch(e) {
      showToast(extractError(e), 'error');
    }
  }

  async function addMasterKey() {
    const otp    = ($('mk-otp-in')?.value  || '').trim();
    const newKey = ($('mk-new-in')?.value  || '').trim();
    if (!otp || !newKey) { showToast('Enter OTP and new key.', 'error'); return; }
    // Client-side strength check
    const { allReqPassed, rules } = _mkStrength(newKey);
    if (!allReqPassed) {
      const failed = rules.find(r => r.req && !r.pass);
      showToast('Key too weak: ' + (failed?.label || 'does not meet requirements') + '.', 'error');
      return;
    }
    try {
      await api('POST', '/api/master_key/add', { otp, new_key: newKey });
      showToast('Master key added.', 'success');
      if ($('mk-current')) $('mk-current').value = '';
      if ($('mk-otp-in'))  $('mk-otp-in').value  = '';
      if ($('mk-new-in'))  $('mk-new-in').value  = '';
      const row = $('mk-otp-row');
      if (row) { row.classList.add('hidden'); row.style.display = 'none'; }
      loadMasterKeys();
    } catch(e) {
      showToast(extractError(e), 'error');
    }
  }

  async function deleteMasterKey(idx) {
    if (!await confirmAction({ title: 'Delete this master key?', body: 'Anyone using it loses admin access and the logs unlock.', confirmLabel: 'Delete' })) return;
    try {
      await api('POST', '/api/master_key/delete', { index: idx });
      loadMasterKeys();
    } catch(e) {
      showToast(extractError(e), 'error');
    }
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
          <td></td>`;
        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn-ghost btn-sm';
        delBtn.textContent = 'Delete';
        delBtn.dataset.phrase = phrase;
        delBtn.addEventListener('click', () => G._delCmd(delBtn.dataset.phrase));
        tr.cells[2].appendChild(delBtn);
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
    if (!phrase || !resp) { showToast('Enter both fields.', 'error'); return; }
    try {
      await api('POST', '/api/config/command/add', { phrase, response: resp });
      closeModal('m-add-cmd'); loadCmds();
    } catch(e) { showToast(extractError(e), 'error'); }
  }

  async function _delCmd(phrase) {
    if (!await confirmAction({ title: 'Delete this command?', body: `"${phrase}"`, confirmLabel: 'Delete' })) return;
    try { await api('POST', '/api/config/command/delete', { phrase }); loadCmds(); }
    catch(e) { showToast(extractError(e), 'error'); }
  }

  // ── Emergency Stop ────────────────────────────────────────
  async function emergencyStop() {
    const ok = await confirmAction({
      title: 'Emergency stop?',
      body: 'This shuts the whole system down: camera, detection and alerts stop until it is started again on the Pi.',
      confirmLabel: 'Stop system',
    });
    if (!ok) return;
    try { await api('POST', '/api/emergency-stop', {}); }
    catch(e) { showToast(extractError(e), 'error'); }
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
  const setText = (id, v) => { const e = $(id); if (e && e.textContent !== String(v)) e.textContent = v; };
  const setWidth = (id, pct) => { const e = $(id); if (e) e.style.width = Math.min(100, Math.max(0, pct)) + '%'; };
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
    el.textContent = (typeof txt === 'string') ? txt : JSON.stringify(txt);
    el.classList.remove('hidden');
  }

  function openDocs() { show('m-docs'); }
  function showMsg(el, txt, ok) {
    el.textContent = txt;
    el.className = 'msg ' + (ok ? 'ok' : 'err');
    el.classList.remove('hidden');
  }
  function showEl(id, txt, ok) { showMsg($(id), txt, ok); }

  async function api(method, url, body, _isRetry = false) {
    const base = getBackend();
    const fullUrl = base ? base.replace(/\/$/, '') + url : url;
    const headers = { 'Content-Type': 'application/json' };
    const tok = _token || (base ? _lsGet('garuda_token') : null);
    if (tok) headers['X-Garuda-Token'] = tok;
    // Sign-out names the refresh token too, so the server can revoke it.
    if (url === '/api/logout' && base && _lsGet('garuda_refresh')) headers['X-Garuda-Refresh'] = _lsGet('garuda_refresh');
    const opts = { method, headers, credentials: base ? 'omit' : 'include' };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(fullUrl, opts);
    let d;
    try { d = await r.json(); } catch(_) { d = { detail: r.statusText || `HTTP ${r.status}` }; }
    if (!r.ok) {
      // On 401, attempt one silent token refresh before giving up
      if (r.status === 401 && !_isRetry && !_loggingOut && !['/api/refresh', '/api/login', '/api/logout'].includes(url)) {
        try {
          const refreshUrl = base ? base.replace(/\/$/, '') + '/api/refresh' : '/api/refresh';
          const stored = base ? _lsGet('garuda_refresh') : null;
          const rr = await fetch(refreshUrl, {
            method: 'POST', credentials: 'include',
            headers: stored ? { 'X-Garuda-Refresh': stored } : {},
          });
          if (rr.ok) {
            const rd = await rr.json();
            if (rd.token) { _token = rd.token; _lsSet('garuda_token', _token); }
            return api(method, url, body, true);   // retry once with new access token
          }
        } catch (_) {}
        // Refresh failed — session is gone, force re-login (no question
        // asked: there is nothing left to stay signed in to).
        if (_session) await _doLogout();
      }
      throw d;
    }
    return d;
  }

  // ── Public API ────────────────────────────────────────────
  return {
    init,
    submitLogin, logout, confirmAction, _confirmAnswer,
    goAdminFlow, backToMain, backToAdminStep1, sendAdminOTP, verifyAdminOTP,
    goMasterKey, submitMasterKeyLogin, unlockLogs,
    goForgot, sendForgotOTP, doReset,
    nav, toggleMode, emergencyStop,
    openBackendConfig, saveBackendConfig,
    toggleMenu, closeMobileMenu,
    toggleTheme, switchCamTab,
    toggleCamera, takeSnapshot, toggleClip, openDocs,
    loadEmailCfg, saveEmail, testEmail,
    loadSysCfg, togglePrivacy, toggleNightPresence, saveSettings,
    filterLogs, exportLogs, downloadFullLog,
    loadDevices, addDevice, deleteDevice, scanNetwork, _regFromScan, refreshPresence,
    loadMasterKeys, requestMkOtp, addMasterKey, deleteMasterKey, onMkKeyInput,
    loadCmds, openAddCmd, addCmd, _delCmd,
    closeModal,
    switchLogTab,
    switchDocsTab,
    showToast,
    setDI: _setDIState,
    syncDI: _syncDIContext,
    product: _PRODUCT,
    // Exposed for the feedback widget (separate IIFE, needs access to session + api)
    getSession: () => _session,
    _apiFn: api,
  };
})();
window.G = G;

document.addEventListener('DOMContentLoaded', G.init);

// Global error handlers — prevent silent crashes during demo
// ── Feedback widget ───────────────────────────────────────────────────────────
// NOTE: This IIFE is outside the G closure. Use G.getSession() and G._apiFn()
// to access session and API — never reference G-internal variables directly.
(function() {
  const _prevAfterLoginHook = G._afterLoginHook || null;
  const _prevFbOnLogout = G._fbOnLogout || null;
  let _fbOpen   = false;
  let _fbRating = 0;
  let _fbCat    = 'general';
  let _fbInbox  = [];
  let _fbFilter = 'all';

  function _isAdmin() {
    const s = G.getSession ? G.getSession() : null;
    return !!(s && s.role === 'admin');
  }

  // ── Build inbox DOM once (lazy) ───────────────────────────
  function _ensureInbox() {
    if (document.getElementById('fb-tab-inbox')) return;
    const panel = document.getElementById('fb-panel');
    if (!panel) return;
    const d = document.createElement('div');
    d.id = 'fb-tab-inbox';
    d.style.display = 'none';
    d.innerHTML =
      '<div class="fb-inbox-toolbar">' +
        '<div class="fb-inbox-summary" id="fb-inbox-summary">Loading…</div>' +
        '<button class="fb-inbox-refresh" onclick="G.fbLoadInbox()" title="Refresh">↻</button>' +
      '</div>' +
      '<div class="fb-cats" id="fb-inbox-filters" style="margin-bottom:8px">' +
        '<span class="fb-cat sel" data-filter="all" onclick="G.fbFilterInbox(this)">All</span>' +
        '<span class="fb-cat" data-filter="bug" onclick="G.fbFilterInbox(this)">Bug</span>' +
        '<span class="fb-cat" data-filter="feature" onclick="G.fbFilterInbox(this)">Feature</span>' +
        '<span class="fb-cat" data-filter="general" onclick="G.fbFilterInbox(this)">General</span>' +
        '<span class="fb-cat" data-filter="other" onclick="G.fbFilterInbox(this)">Other</span>' +
      '</div>' +
      '<div class="fb-inbox-list" id="fb-inbox-list">' +
        '<div class="fb-inbox-empty">No feedback yet.</div>' +
      '</div>';
    panel.appendChild(d);
  }

  // ── Show correct view for current role ────────────────────
  function _applyRole() {
    const admin = _isAdmin();
    const sendTab  = document.getElementById('fb-tab-send');
    const tabBar   = document.getElementById('fb-tabs');
    // Tab bar is never shown — each role gets exactly one view
    if (tabBar) tabBar.style.display = 'none';
    if (admin) {
      _ensureInbox();
      if (sendTab)  sendTab.style.display  = 'none';
      const it = document.getElementById('fb-tab-inbox');
      if (it) it.style.display = '';
    } else {
      if (sendTab)  sendTab.style.display  = '';
      const it = document.getElementById('fb-tab-inbox');
      if (it) it.style.display = 'none';
    }
  }

  // ── Open / close ──────────────────────────────────────────
  G.toggleFeedback = function() {
    _fbOpen = !_fbOpen;
    document.getElementById('fb-panel').classList.toggle('open', _fbOpen);
    document.getElementById('fb-bubble').classList.toggle('is-open', _fbOpen);
    if (_fbOpen) {
      if (_isAdmin()) G.fbLoadInbox();
      else {
        const m = document.getElementById('fb-msg');
        if (m) m.focus();
      }
    }
  };

  // Close on outside click
  document.addEventListener('pointerdown', e => {
    if (!_fbOpen) return;
    const wrap = document.getElementById('fb-wrap');
    if (wrap && !wrap.contains(e.target)) {
      _fbOpen = false;
      document.getElementById('fb-panel').classList.remove('open');
      document.getElementById('fb-bubble').classList.remove('is-open');
    }
  });

  // ── After-login hook (called from afterLogin in G) ────────
  G._afterLoginHook = function(session) {
    if (_prevAfterLoginHook) _prevAfterLoginHook(session);
    _applyRole();
  };

  // ── Logout hook (called from logout in G) ─────────────────
  G._fbOnLogout = function() {
    if (_prevFbOnLogout) _prevFbOnLogout();
    _fbOpen = false;
    _fbRating = 0; _fbCat = 'general'; _fbInbox = []; _fbFilter = 'all';
    document.getElementById('fb-panel').classList.remove('open');
    document.getElementById('fb-bubble').classList.remove('is-open');
    // Reset to send view for next login
    const sendTab  = document.getElementById('fb-tab-send');
    const inboxTab = document.getElementById('fb-tab-inbox');
    if (sendTab)  sendTab.style.display  = '';
    if (inboxTab) inboxTab.style.display = 'none';
    // Clear send form
    const msgEl = document.getElementById('fb-msg');
    const nameEl = document.getElementById('fb-name');
    const charEl = document.getElementById('fb-char');
    const bodyEl = document.getElementById('fb-form-body');
    const succEl = document.getElementById('fb-success');
    if (msgEl)  msgEl.value  = '';
    if (nameEl) nameEl.value = '';
    if (charEl) { charEl.textContent = '0 / 1000'; charEl.className = 'fb-char-count'; }
    if (bodyEl) bodyEl.style.display = '';
    if (succEl) succEl.classList.remove('show');
    _previewStars(0);
  };

  // ── Tab switching (kept for G.fbSwitchTab compatibility) ──
  G.fbSwitchTab = function() {}; // no-op — roles are fixed per session

  // ── Inbox load ────────────────────────────────────────────
  G.fbLoadInbox = async function() {
    if (!_isAdmin()) return;
    const btn = document.querySelector('.fb-inbox-refresh');
    if (btn) { btn.classList.add('spinning'); setTimeout(() => btn.classList.remove('spinning'), 600); }
    try {
      const data = await G._apiFn('GET', '/api/feedback');
      _fbInbox = (data.feedback || []).slice().reverse();
      _fbFilter = 'all';
      document.querySelectorAll('#fb-inbox-filters .fb-cat')
        .forEach(c => c.classList.toggle('sel', c.dataset.filter === 'all'));
      _fbRenderInbox();
    } catch(e) {
      const el = document.getElementById('fb-inbox-summary');
      if (el) el.textContent = 'Failed to load.';
    }
  };

  G.fbFilterInbox = function(el) {
    _fbFilter = el.dataset.filter;
    document.querySelectorAll('#fb-inbox-filters .fb-cat').forEach(c => c.classList.toggle('sel', c === el));
    _fbRenderInbox();
  };

  function _fbRenderInbox() {
    const list    = document.getElementById('fb-inbox-list');
    const summary = document.getElementById('fb-inbox-summary');
    if (!list || !summary) return;
    const entries = _fbFilter === 'all' ? _fbInbox : _fbInbox.filter(e => e.category === _fbFilter);
    const total = _fbInbox.length;
    const rated = _fbInbox.filter(e => e.rating > 0);
    const avg   = rated.length ? (rated.reduce((s, e) => s + e.rating, 0) / rated.length).toFixed(1) : null;
    summary.textContent = total + ' entr' + (total === 1 ? 'y' : 'ies') + (avg ? ' · avg ' + avg + ' ★' : '');
    if (!entries.length) {
      list.innerHTML = '<div class="fb-inbox-empty">' +
        (_fbFilter === 'all' ? 'No feedback yet.' : 'No entries for this category.') + '</div>';
      return;
    }
    // Use G.showToast's esc helper via the exposed escHtml or reimplement inline
    function esc(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }
    list.innerHTML = entries.map(e => {
      const stars = e.rating > 0 ? '★'.repeat(e.rating) + '☆'.repeat(5 - e.rating) : '';
      return '<div class="fb-card">' +
        '<div class="fb-card-header">' +
          '<span class="fb-card-name">' + esc(e.name || 'Anonymous') + '</span>' +
          (stars ? '<span class="fb-card-stars">' + stars + '</span>' : '') +
        '</div>' +
        '<div class="fb-card-meta">' +
          '<span class="fb-card-cat ' + esc(e.category) + '">' + esc(e.category) + '</span>' +
          '<span class="fb-card-time">' + esc(e.timestamp) + '</span>' +
        '</div>' +
        '<div class="fb-card-msg">' + esc(e.message) + '</div>' +
      '</div>';
    }).join('');
  }

  // ── Stars (fully JS-managed) ──────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.fb-star').forEach(s => {
      const v = parseInt(s.dataset.v);
      s.addEventListener('pointerenter', () => _previewStars(v));
      s.addEventListener('pointerleave', () => _previewStars(0));
      s.addEventListener('click', e => { e.preventDefault(); _fbRating = v; _previewStars(0); });
      s.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _fbRating = v; _previewStars(0); }
      });
    });
    document.querySelectorAll('#fb-tab-send .fb-cat').forEach(el => {
      el.addEventListener('click', () => {
        document.querySelectorAll('#fb-tab-send .fb-cat').forEach(c => c.classList.remove('sel'));
        el.classList.add('sel');
        _fbCat = el.dataset.cat;
      });
    });
    const msg = document.getElementById('fb-msg');
    const counter = document.getElementById('fb-char');
    if (msg && counter) {
      msg.addEventListener('input', () => {
        const n = msg.value.length;
        counter.textContent = n + ' / 1000';
        counter.className = 'fb-char-count' + (n > 900 ? (n >= 1000 ? ' over' : ' near') : '');
      });
    }
  });

  function _previewStars(preview) {
    const show = preview > 0 ? preview : _fbRating;
    document.querySelectorAll('.fb-star').forEach(s => {
      const v = parseInt(s.dataset.v);
      s.classList.toggle('lit',          !preview && v <= show);
      s.classList.toggle('hover-preview', preview  && v <= show);
    });
  }

  // ── Reset send form ───────────────────────────────────────
  function _resetForm(btn) {
    const body = document.getElementById('fb-form-body');
    const success = document.getElementById('fb-success');
    const msg = document.getElementById('fb-msg');
    const name = document.getElementById('fb-name');
    const char = document.getElementById('fb-char');
    if (body) body.style.display = '';
    if (success) success.classList.remove('show');
    if (msg) msg.value = '';
    if (name) name.value = '';
    if (char) {
      char.textContent = '0 / 1000';
      char.className = 'fb-char-count';
    }
    _fbRating = 0; _fbCat = 'general';
    _previewStars(0);
    document.querySelectorAll('#fb-tab-send .fb-cat')
      .forEach(c => c.classList.toggle('sel', c.dataset.cat === 'general'));
    if (btn) { btn.disabled = false; btn.textContent = 'Send'; }
  }

  function _showFeedbackSuccess(btn) {
    const body = document.getElementById('fb-form-body');
    const success = document.getElementById('fb-success');
    const panel = document.getElementById('fb-panel');
    const bubble = document.getElementById('fb-bubble');
    if (body) body.style.display = 'none';
    if (success) success.classList.add('show');
    setTimeout(() => {
      _fbOpen = false;
      if (panel) panel.classList.remove('open');
      if (bubble) bubble.classList.remove('is-open');
      setTimeout(() => _resetForm(btn), 400);
    }, 1400);
  }

  // ── Submit ────────────────────────────────────────────────
  G.submitFeedback = async function() {
    const msgEl = document.getElementById('fb-msg');
    const nameEl = document.getElementById('fb-name');
    const msgVal  = msgEl ? msgEl.value.trim() : '';
    const nameVal = nameEl ? nameEl.value.trim() : '';
    if (!msgVal) { if (msgEl) msgEl.focus(); return; }

    const btn = document.getElementById('fb-submit');
    const payload    = { message: msgVal, category: _fbCat, rating: _fbRating, name: nameVal };
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Sending…';
    }

    try {
      await G._apiFn('POST', '/api/feedback', payload);
      _showFeedbackSuccess(btn);
    } catch(err) {
      const detail = (err && (err.detail || err.message)) || 'Failed to send feedback.';
      G.showToast(detail, 'error');
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Send';
      }
    }
  };
})();

window.addEventListener('unhandledrejection', e => {
  console.warn('[Garuda] Unhandled promise:', e.reason);
});
window.addEventListener('error', e => {
  console.warn('[Garuda] Runtime error:', e.message);
});

// Close modal on overlay click
document.addEventListener('click', e => {
  if (!e.target.classList || !e.target.classList.contains('modal-overlay')) return;
  if (e.target.id === 'm-confirm') G._confirmAnswer(false);   // also settles the pending question
  else e.target.classList.add('hidden');
});
document.addEventListener('keydown', e => {
  const ov = document.getElementById('m-confirm');
  if (!ov || ov.classList.contains('hidden')) return;
  if (e.key === 'Escape') { e.preventDefault(); G._confirmAnswer(false); }
  // Enter must not fall through to the login / logs-gate shortcuts below.
  if (e.key === 'Enter') e.stopImmediatePropagation();
}, true);

// Enter key shortcuts — logs-gate works while logged in; login views only before login
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const lg = document.getElementById('logs-gate');
  if (lg && !lg.classList.contains('hidden')) { G.unlockLogs(); return; }
  // All remaining shortcuts are for the login screen only
  if (document.getElementById('app').classList.contains('logged-in')) return;
  const lv1 = document.getElementById('lv-main');
  const lv2 = document.getElementById('lv-admin-1');
  const lv3 = document.getElementById('lv-admin-2');
  const lvm = document.getElementById('lv-masterkey');
  if (lvm && !lvm.classList.contains('hidden')) G.submitMasterKeyLogin();
  else if (lv1 && !lv1.classList.contains('hidden')) G.submitLogin();
  else if (lv2 && !lv2.classList.contains('hidden')) G.sendAdminOTP();
  else if (lv3 && !lv3.classList.contains('hidden')) G.verifyAdminOTP();
});

document.getElementById('app')?.classList.add('fb-hidden');

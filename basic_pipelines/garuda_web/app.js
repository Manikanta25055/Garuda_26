/* ============================================================
   Garuda — SPA logic
   ============================================================ */
// Tag the page with what it runs on, before anything paints (style.css,
// "Every browser, every device"). Apple's own browsers keep the full glass.
(function () {
  try {
    const ua = navigator.userAgent || '';
    // Apple's own engine draws blur and glass on the GPU almost for free:
    // every browser on an iPhone or iPad, and Safari on a Mac. Chrome, Brave,
    // Edge and Firefox on a Mac were counted here too (their user agents say
    // "Macintosh" and "Safari/"), and got the full glass they stutter on.
    const iOS = /iPhone|iPad|iPod/.test(ua)
      || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);   // iPadOS asks for the desktop site
    const macSafari = /Macintosh/.test(ua) && /Safari\//.test(ua)
      && !/(Chrome|Chromium|CriOS|Edg|OPR|Brave|Firefox|FxiOS)\//.test(ua);
    const apple = iOS || macSafari;
    const root = document.documentElement;
    if (!apple) root.classList.add('plat-other');
    // Phones and tablets that are not Apple's get the light rendering from
    // the start: blur and layered shadows are what made scrolling stutter
    // there, and waiting to measure it meant a laggy first minute.
    const touchOnly = window.matchMedia && window.matchMedia('(hover: none) and (pointer: coarse)').matches;
    if (!apple && touchOnly) root.classList.add('perf-lite');
    const slowHint = (navigator.deviceMemory && navigator.deviceMemory <= 2)
      || (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2)
      || (window.matchMedia && window.matchMedia('(prefers-reduced-transparency: reduce)').matches);
    if (slowHint) root.classList.add('perf-lite');
  } catch (_) {}
})();

// Measure real frame times once the app is on screen; a device that cannot
// hold ~40 fps (median frame over 25 ms, or one frame in ten over 50 ms)
// drops the blur and decoration for this visit. It waited for ~25 fps, by
// which point the stutter was plain to see. Apple devices
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
    if (gaps[gaps.length >> 1] > 25 || gaps[Math.floor(gaps.length * 0.9)] > 50) root.classList.add('perf-lite');
  }
  requestAnimationFrame(step);
}

const G = (() => {

  // ── State ────────────────────────────────────────────────
  let _session = null;   // { role, username, display_name }
  // The access token, for a front end on another site that cannot use the
  // Pi's cookies. Memory only: it was kept in localStorage with the refresh
  // token, where any script on the page could read a week-long sign-in.
  let _token   = null;
  let _ws      = null;
  let _pendingAdmin = null;  // { username } during OTP flow
  let _prevAlertActive = false;
  let _lastDetInfo = '';
  let _recentDets  = [];
  let _privacyOn = true;
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
  let _audioCtx       = null; // the alarm's fallback beep, made when first needed
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
    window.Feel?.toast(type);
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
    if (s.inference_fps != null) setText('hud-fps', Math.round(s.inference_fps) + ' fps');
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
    // Tokens an older version stored where scripts can read them.
    _lsDel('garuda_token');
    _lsDel('garuda_refresh');

    // Try to restore the session from a previous visit. On a page the Pi
    // serves the session cookie is enough; a front end on another site first
    // trades its httpOnly refresh cookie for an access token held in memory.
    const canRestore = samePi || (!!backend && await _refreshAccess().catch(() => false));
    if (canRestore) {
      try {
        const session = await api('GET', '/api/session');
        _session = session;
        afterLogin();
        return;
      } catch(e) {
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
  // session cookie, so it holds the access token in memory and sends it as a
  // header. The refresh token stays in its httpOnly cookie.
  function _storeAuth(res) {
    if (!getBackend()) return;
    if (res.token) _token = res.token;
  }

  // A fresh access token from the refresh cookie, which no script can read.
  // True when the cookie was still good.
  async function _refreshAccess() {
    const base = getBackend();
    const url = (base ? base.replace(/\/$/, '') : '') + '/api/refresh';
    const r = await fetch(url, { method: 'POST', credentials: 'include' });
    if (!r.ok) return false;
    const d = await r.json();
    if (base && d.token) _token = d.token;
    return true;
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
      window.Feel?.play('unlock');
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
      window.Feel?.play('unlock');
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
      window.Feel?.play('unlock');
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
    // Activity: today, local time; any earlier day is read from the logs on disk.
    _initTimeline();
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
    // No AudioContext is opened here any more: one resumed at sign-in ran for
    // the rest of the visit, and a running context lags Safari's transitions.
    // sound.js unlocks audio in the first tap and plays the alarm; _beep()
    // makes its own context only if that sound is missing.
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
    if (o.danger !== false) window.Feel?.touch('warning');
    setTimeout(() => $('m-confirm-cancel')?.focus(), 30);
    return new Promise(resolve => {
      _confirmDone = result => {
        _confirmDone = null;
        ov.classList.add('hidden');
        // What the confirmed act sounds like: o.sound, or the trash for a
        // Delete / Remove. Nothing plays for Cancel.
        const cue = o.sound || (/^(delete|remove)\b/i.test(ok.textContent) ? 'delete' : null);
        if (result && cue) window.Feel?.play(cue);
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
    window.Feel?.play('lock');
    try { await _doLogoutInner(); } finally { _loggingOut = false; }
  }

  async function _doLogoutInner() {
    // The conversation (and the keys to its artifact pages) is kept for the
    // tab, and on the server; the next person to sign in here must not get it
    // back. Cleared first, while the sign-in still lets it reach the server.
    if (window.N && N.clearSession) N.clearSession();
    try { await api('POST', '/api/logout', {}); } catch(_) {}
    // The refresh cookie is sent only under /api/refresh, so /api/logout never
    // sees it; this ends it on the server as well as in the browser.
    try {
      const base = getBackend();
      await fetch((base ? base.replace(/\/$/, '') : '') + '/api/refresh/revoke',
                  { method: 'POST', credentials: 'include' });
    } catch (_) {}
    if (window.N) { N.stopVoice(); N.closeArtifact && N.closeArtifact(); }
    // Clear uptime interval before resetting state
    if (_uptimeInterval) { clearInterval(_uptimeInterval); _uptimeInterval = null; }
    _wsAllowed = false;   // prevent reconnect after logout
    _stopAlarm();
    if (G._fbOnLogout) G._fbOnLogout();   // clean up feedback inbox tab
    _session = null; _token = null; _logsUnlocked = false;
    _recentDets = []; _prevAlertActive = false; _lastAlertState = false; _lastDetInfo = '';
    _timelineSig = ''; _timelineItems = []; _timelineReq++;
    _logLines = { system: [], detection: [], voice: [], presence: [] }; _logsLoaded = false;
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
    const isLive = txt.startsWith('Live');
    if (dot)   dot.className = 'cam-live-dot ' + (isLive ? 'cam-dot-live' : 'cam-dot-off');
    if (label) label.textContent = isLive ? 'Live' : 'Connect';
    // Show/hide snapshot + record buttons
    const snapBtn = $('cam-snapshot-btn');
    const recBtn  = $('cam-record-btn');
    if (snapBtn) snapBtn.style.display = isLive ? '' : 'none';
    if (recBtn)  recBtn.style.display  = isLive ? '' : 'none';
    const sndBtn = $('cam-sound-btn');
    if (sndBtn)  sndBtn.style.display  = isLive ? '' : 'none';
  }

  function stopCameraStream() {
    if (_mjpegRetry) { clearTimeout(_mjpegRetry); _mjpegRetry = null; }
    const camImg   = $('cam-img');
    const camVideo = $('cam-video');
    if (camImg)   { camImg.onload = null; camImg.onerror = null; camImg.src = ''; camImg.style.display = 'none'; }
    if (camVideo) { camVideo.srcObject = null; camVideo.style.display = 'none'; }
    if (_activePc) { _activePc.close(); _activePc = null; }
    if (_wsStream) { _wsStream.close(); _wsStream = null; }
    stopFeedAudio();
  }

  // ── Camera sound (/ws/audio) ──────────────────────────────
  // Its own socket, so a big video frame never holds sound up. The worklet
  // keeps ~60 ms against jitter and skips ahead rather than fall behind.
  // Off = socket closed: no microphone, no bandwidth.
  // It plays through the interface sounds' context (Feel.hold), not one of
  // its own: a second running context was a second real-time audio thread,
  // and clicks then had to wake theirs beside it, which made them late. It
  // plays only while the picture is on screen (dashboard, tab visible), so
  // audio does not keep running behind the other pages.
  let _feedAudio = null;   // { ctx, node, ws, retry }
  let _feedModule = null;  // the worklet, added to the shared context once
  const _FEED_SOUND_KEY = 'garuda_feed_sound';

  function _feedSoundWanted() { return _lsGet(_FEED_SOUND_KEY) !== 'off'; }

  function _feedSoundUi(state) {     // 'on' | 'off' | 'blocked' | 'none'
    const btn = $('cam-sound-btn');
    if (!btn) return;
    const on = state === 'on';
    btn.classList.toggle('is-muted', !on);
    btn.classList.toggle('is-unavailable', state === 'none');
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    const t = { on: 'Sound on', off: 'Sound off', blocked: 'Tap for sound',
                none: 'No microphone' }[state];
    btn.title = t; btn.setAttribute('aria-label', t);
  }

  function _wsUrl(path) {
    const base = (getBackend() || location.origin).replace(/^http/, 'ws');
    return base + path + (_token ? `?token=${encodeURIComponent(_token)}` : '');
  }

  // The camera card is what is being looked at.
  function _camOnScreen() { return _currentPage === 'dashboard' && !document.hidden; }

  function startFeedAudio() {
    if (_feedAudio) return;
    const ctx = window.Feel && Feel.hold ? Feel.hold(true) : null;
    if (!ctx || !ctx.audioWorklet || !window.AudioWorkletNode) {
      if (window.Feel && Feel.hold) Feel.hold(false);
      _feedSoundUi('none');
      return;
    }
    const fa = _feedAudio = { ctx, node: null, ws: null, retry: null };
    // The worklet loads while the socket opens; neither waits for the other.
    if (!_feedModule) {
      _feedModule = ctx.audioWorklet.addModule('/static/audio-feed-worklet.js?v=2');
      _feedModule.catch(() => { _feedModule = null; });
    }
    const ready = _feedModule;
    ready.catch((e) => {
      console.warn('camera sound: worklet failed', e);
      if (_feedAudio === fa) { stopFeedAudio(); _feedSoundUi('none'); }
    });
    ctx.resume().catch(() => {}).finally(() => {
      if (_feedAudio === fa) _feedSoundUi(ctx.state === 'running' ? 'on' : 'blocked');
    });

    const open = () => {
      if (_feedAudio !== fa) return;
      const ws = new WebSocket(_wsUrl('/ws/audio'));
      ws.binaryType = 'arraybuffer';
      fa.ws = ws;
      ws.onmessage = (ev) => {
        if (typeof ev.data === 'string') {       // the format header
          let fmt = {};
          try { fmt = JSON.parse(ev.data); } catch (_) {}
          ready.then(() => {
            if (_feedAudio !== fa || fa.ws !== ws) return;
            if (fa.node) fa.node.disconnect();
            fa.node = new AudioWorkletNode(ctx, 'feed-player', {
              outputChannelCount: [1],
              processorOptions: { inputRate: fmt.rate || 16000, targetMs: 60, maxMs: 180 },
            });
            fa.node.connect(ctx.destination);
          }, () => {});
          return;
        }
        // The worklet turns the samples into floats, off this thread.
        if (fa.node) fa.node.port.postMessage(ev.data, [ev.data]);
      };
      ws.onclose = (ev) => {
        if (_feedAudio !== fa || fa.ws !== ws) return;
        fa.ws = null;
        if (ev.code === 4503) { _feedSoundUi('none'); return; }   // no microphone on the Pi
        if (ev.code === 4001) return;                              // signed out
        fa.retry = setTimeout(open, 2000);
      };
    };
    open();
  }

  function stopFeedAudio() {
    const fa = _feedAudio;
    _feedAudio = null;
    if (!fa) return;
    if (fa.retry) clearTimeout(fa.retry);
    if (fa.ws) { try { fa.ws.close(); } catch (_) {} }
    if (fa.node) { try { fa.node.disconnect(); } catch (_) {} }
    // The context is shared: released, not closed; it rests on its own.
    if (window.Feel && Feel.hold) Feel.hold(false);
  }

  // Sound follows the picture: off while another page or tab is in front,
  // back (if it was wanted) when the camera card is again.
  function _syncFeedAudio() {
    const live = $('cam-toggle-btn')?.classList.contains('active');
    if (live && _camOnScreen()) {
      if (!_feedAudio && _feedSoundWanted() && !$('cam-sound-btn')?.classList.contains('is-unavailable')) startFeedAudio();
    } else if (_feedAudio) {
      stopFeedAudio();
    }
  }

  function toggleFeedSound() {
    // Starting here, inside the tap, is what lets the browser play sound.
    if (_feedAudio && _feedAudio.ctx && _feedAudio.ctx.state !== 'running') {
      _feedAudio.ctx.resume().then(() => _feedSoundUi('on')).catch(() => {});
      _lsSet(_FEED_SOUND_KEY, 'on');
      return;
    }
    if (_feedAudio) {
      stopFeedAudio();
      _lsSet(_FEED_SOUND_KEY, 'off');
      _feedSoundUi('off');
    } else {
      _lsSet(_FEED_SOUND_KEY, 'on');
      startFeedAudio();
    }
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
    const camImg   = $('cam-img');
    const camOffline = $('cam-offline');
    // ack=1: each frame is answered on arrival and the Pi keeps at most two
    // unanswered, so a slow link skips frames instead of queuing them (lag).
    const ws       = new WebSocket(_wsUrl('/ws/stream') + (_token ? '&' : '?') + 'ack=1');
    ws.binaryType  = 'arraybuffer';
    _wsStream      = ws;
    let connected  = false;
    let pending    = null;    // newest frame not yet shown; older ones are skipped
    let busy       = false;
    const show = async () => {
      if (busy || !pending || !camImg) return;
      busy = true;
      const blob = pending; pending = null;
      const url = URL.createObjectURL(blob);
      const im = new Image();
      im.src = url;
      try { await im.decode(); } catch (_) { URL.revokeObjectURL(url); busy = false; show(); return; }
      if (_wsStream !== ws) { URL.revokeObjectURL(url); busy = false; return; }
      const old = camImg.src;
      camImg.src = url;
      if (old.startsWith('blob:')) URL.revokeObjectURL(old);
      if (!connected) {
        connected = true;
        camImg.style.display = 'block';
        if (camOffline) camOffline.style.display = 'none';
        _camSetStatus('Live · WS');
      }
      busy = false;
      show();
    };
    ws.onopen = () => { _camSetStatus('Connecting…'); };
    ws.onmessage = (ev) => {
      try { ws.send('a'); } catch (_) {}
      // Behind another page or tab nobody sees the picture: decoding every
      // frame there only took time from the page that is in front.
      if (connected && !_camOnScreen()) { pending = null; return; }
      pending = new Blob([ev.data], { type: 'image/jpeg' });
      show();
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
      // WS frames with acks: an MJPEG response cannot skip frames, so on a
      // slow link it queues them and the picture drifts behind the room.
      // MJPEG stays as the fallback when the socket cannot open.
      startWsStream();
      // This tap is the gesture that lets the camera's sound play.
      if (_feedSoundWanted()) startFeedAudio(); else _feedSoundUi('off');
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
        window.Feel?.play('rec-start');
        showToast('Recording started — auto-stops at 60 s.', 'success');
      } else {
        const r = await api('POST', '/api/clip/stop');
        _clipRecording = false;
        if (btn) { btn.innerHTML = _REC_ICON.rec; btn.classList.remove('recording'); }
        window.Feel?.play('rec-stop');
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

  // How many weeks, and how large a square, the card has room for. Beside
  // the console (desktop) the card is as wide as 13 weeks of 10 px squares.
  // When it has the whole row to itself (a phone, or no console) those 13
  // weeks filled half of it; it now reaches further back in time, older weeks
  // to the left, and the squares grow a little so the last one meets the edge.
  const _HM = { cell: 10, gap: 3, label: 28, weeks: 13, maxWeeks: 53, maxCell: 16 };
  let _hmActivity = {}, _hmFit = '', _hmObserved = false;
  function _heatmapFit(container) {
    const card = container.closest('.hm-card');
    const row = card && card.parentElement;
    const tracks = row ? getComputedStyle(row).gridTemplateColumns.trim().split(/\s+/).length : 2;
    const room = container.clientWidth - _HM.label;
    if (tracks !== 1 || room <= 0) return { weeks: _HM.weeks, cell: _HM.cell };
    const weeks = Math.max(_HM.weeks, Math.min(_HM.maxWeeks, Math.floor((room + _HM.gap) / (_HM.cell + _HM.gap))));
    const cell = Math.min(_HM.maxCell, Math.floor(((room - (weeks - 1) * _HM.gap) / weeks) * 100) / 100);
    return { weeks, cell };
  }

  function renderHeatmap(activity) {
    activity = activity || {};
    _hmActivity = activity;
    const container = $('heatmap');
    if (!container) return;
    if (!_hmObserved && window.ResizeObserver) {
      // A rotated phone or a resized window: draw again only if the fit changed.
      _hmObserved = true;
      new ResizeObserver(() => {
        const f = _heatmapFit(container);
        if (f.weeks + ':' + f.cell !== _hmFit) renderHeatmap(_hmActivity);
      }).observe(container);
    }

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const fit = _heatmapFit(container);
    _hmFit = fit.weeks + ':' + fit.cell;
    const CELL = fit.cell, GAP = _HM.gap, COL_W = CELL + GAP;
    const NUM_WEEKS = fit.weeks;
    container.style.setProperty('--hm-cell', CELL + 'px');
    const meta = container.closest('.hm-card')?.querySelector('.dash-card-meta');
    if (meta) meta.textContent = `${NUM_WEEKS} weeks · local`;

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

  // ── The tab bar's selection pill ──────────────────────────
  // A lens that sits under the current tab. A tap sends it there; a press
  // and drag (mouse, pen or finger) picks it up and carries it across the
  // bar, and letting go throws it to the tab it is heading for, which is
  // then opened. Motion is a real spring, not a CSS curve: it is integrated
  // per frame, so a throw keeps the hand's velocity and a tap that lands
  // mid-flight bends the path instead of restarting it. Springs are given
  // as SwiftUI gives them, a response (seconds) and a damping fraction.
  const _RM = window.matchMedia('(prefers-reduced-motion: reduce)');
  const _SPRING = {
    tap:     { response: 0.38, damping: 0.80 },   // a tab was tapped
    release: { response: 0.42, damping: 0.74 },   // let go: a little settle
    follow:  { response: 0.11, damping: 1.00 },   // under the hand: tight, never bouncy
    lift:    { response: 0.22, damping: 0.52 },   // the bulge: quick, with a jelly wobble
  };
  const _pill = {
    x: 6, w: 68, vx: 0, vw: 0, tx: 6, tw: 68,     // left edge and width, in the bar's content
    lift: 0, vl: 0, tl: 0,                         // 0 resting, 1 picked up
    spring: _SPRING.tap, raf: 0, last: 0,
    thrown: false,                                 // the next move carries on from a throw
  };

  // Where the pill sits for a tab: a little wider than the tab, and kept
  // inside the bar, since the first and last tabs sit near its rounded ends.
  function _pillSpot(navEl, itemEl) {
    const nr = navEl.getBoundingClientRect();
    const ir = itemEl.getBoundingClientRect();
    const ovr = 8, inset = 4, maxX = navEl.scrollWidth - inset;
    // getBoundingClientRect() is viewport-relative; the pill is positioned
    // in the nav's scrollable content, so scrollLeft is added back.
    let x = ir.left - nr.left + navEl.scrollLeft - ovr;
    let w = ir.width + ovr * 2;
    if (x < inset) { w -= inset - x; x = inset; }
    if (x + w > maxX) w = maxX - x;
    return { x, w };
  }

  function _pillDraw() {
    const el = $('ios-pill'); if (!el) return;
    const p = _pill;
    // Touched, the lens bulges up under the finger; moving fast, it
    // stretches along its path and thins, and gives the shape back as it
    // slows. Let go, it springs back with a small wobble.
    let sx = 1, sy = 1;
    if (!_RM.matches) {
      const stretch = Math.min(Math.abs(p.vx) / 6000, 0.10);
      sx = 1 + 0.10 * p.lift + stretch;
      sy = 1 + 0.16 * p.lift - stretch * 0.6;
    }
    el.style.width = p.w.toFixed(2) + 'px';
    el.style.transform = `translate3d(${p.x.toFixed(2)}px,0,0) scale(${sx.toFixed(4)},${sy.toFixed(4)})`;
  }

  function _pillStep(now) {
    const p = _pill;
    const dt = Math.min(0.05, Math.max(0, (now - (p.last || now)) / 1000));
    p.last = now;
    // Fixed small steps keep the spring the same at 60 and 120 Hz.
    for (let t = 0; t < dt; t += 1 / 240) {
      const h = Math.min(1 / 240, dt - t);
      const sp = p.spring, kx = Math.pow(2 * Math.PI / sp.response, 2), cx = 4 * Math.PI * sp.damping / sp.response;
      p.vx += (-kx * (p.x - p.tx) - cx * p.vx) * h; p.x += p.vx * h;
      p.vw += (-kx * (p.w - p.tw) - cx * p.vw) * h; p.w += p.vw * h;
      const sl = _SPRING.lift, kl = Math.pow(2 * Math.PI / sl.response, 2), cl = 4 * Math.PI * sl.damping / sl.response;
      p.vl += (-kl * (p.lift - p.tl) - cl * p.vl) * h; p.lift += p.vl * h;
    }
    _pillDraw();
    const still = Math.abs(p.x - p.tx) < 0.05 && Math.abs(p.vx) < 0.5
      && Math.abs(p.w - p.tw) < 0.05 && Math.abs(p.vw) < 0.5
      && Math.abs(p.lift - p.tl) < 0.002 && Math.abs(p.vl) < 0.01;
    if (still && !_pillDrag) {
      p.x = p.tx; p.w = p.tw; p.lift = p.tl; p.vx = p.vw = p.vl = 0;
      _pillDraw(); p.raf = 0; p.last = 0;
      return;
    }
    p.raf = requestAnimationFrame(_pillStep);
  }

  function _pillGo(x, w, spring, instant) {
    const p = _pill;
    p.tx = x; p.tw = w; p.spring = spring || _SPRING.tap;
    if (instant || _RM.matches) {
      p.x = x; p.w = w; p.vx = p.vw = 0;
      if (!p.raf) { p.lift = p.tl; p.vl = 0; _pillDraw(); return; }
    }
    if (!p.raf) { p.last = 0; p.raf = requestAnimationFrame(_pillStep); }
  }

  function movePill(itemEl, instant) {
    const navEl = $('ios-nav');
    if (!$('ios-pill') || !navEl || !itemEl) return;
    const { x, w } = _pillSpot(navEl, itemEl);
    _pillGo(x, w, _pill.thrown ? _SPRING.release : _SPRING.tap, instant);
    _pill.thrown = false;
  }

  // ── Dragging the pill ──
  let _pillDrag = null;          // the press in progress
  let _pillEatClick = false;     // a drag ended: the click that follows is not a tap
  let _pillUnpress = 0;          // a quick tap's bulge, waiting to settle

  function _pillTabs(navEl) {
    return [...navEl.querySelectorAll('.ios-item')].map(el => {
      const s = _pillSpot(navEl, el);
      return { el, x: s.x, w: s.w, c: s.x + s.w / 2, danger: el.classList.contains('ios-danger') };
    });
  }

  // UIScrollView's rubber band: past the end, the pill gives less and less.
  const _rubber = (d, dim) => (1 - 1 / (d * 0.55 / dim + 1)) * dim;

  function _pillContentX(navEl, clientX) {
    return clientX - navEl.getBoundingClientRect().left + navEl.scrollLeft;
  }

  function _pillPeek(navEl, tab) {
    if (_pillDrag.peek === tab) return;
    _pillDrag.peek?.el.classList.remove('ios-peek');
    _pillDrag.peek = tab;
    tab?.el.classList.add('ios-peek');
    if (tab) window.Feel?.touch('selection');   // a detent as it crosses each tab
  }

  // The tab nearest a point on the bar. The Stop button is never a place to
  // land: dropping on it must not stop the house.
  function _pillNearest(tabs, c) {
    let best = null;
    for (const t of tabs) {
      if (t.danger) continue;
      if (!best || Math.abs(t.c - c) < Math.abs(best.c - c)) best = t;
    }
    return best;
  }

  function _pillFollow(navEl, clientX) {
    const d = _pillDrag, tabs = d.tabs, first = tabs[0], end = tabs[tabs.length - 1];
    let c = _pillContentX(navEl, clientX) - d.grab;
    // Past the first or last tab the pill holds back, as a list does.
    if (c < first.c) c = first.c - _rubber(first.c - c, 48);
    else if (c > end.c) c = end.c + _rubber(c - end.c, 48);
    // Its width blends between the two tabs it is over.
    let w = first.w;
    for (let i = 0; i < tabs.length - 1; i++) {
      const a = tabs[i], b = tabs[i + 1];
      if (c >= a.c && c <= b.c) { const t = (c - a.c) / (b.c - a.c || 1); w = a.w + (b.w - a.w) * t; break; }
      if (c > b.c) w = b.w;
    }
    _pillGo(c - w / 2, w, _SPRING.follow);
    _pillPeek(navEl, _pillNearest(tabs, c));
  }

  function _bindPillDrag(navEl) {
    if (navEl.dataset.pillDrag) return;
    navEl.dataset.pillDrag = '1';

    navEl.addEventListener('pointerdown', e => {
      if (e.button !== 0 || !e.isPrimary || _pillDrag) return;
      if (!e.target.closest('.ios-item')) return;
      const tabs = _pillTabs(navEl);
      if (tabs.length < 2) return;
      const px = _pillContentX(navEl, e.clientX);
      const p = _pill, onPill = px >= p.x && px <= p.x + p.w;
      _pillDrag = {
        id: e.pointerId, x0: e.clientX, y0: e.clientY, on: false, tabs, peek: null,
        // Picked up by its side, the pill stays under the hand where it was held.
        grab: onPill ? px - (p.x + p.w / 2) : 0,
        trail: [{ x: e.clientX, t: e.timeStamp }],
      };
      // Pressing the lens itself bulges it at once, as on iOS.
      if (onPill) {
        _pillDrag.pressAt = e.timeStamp;
        clearTimeout(_pillUnpress);
        $('ios-pill')?.classList.add('ios-pill-lifted');
        p.tl = 1; _pillGo(p.tx, p.tw, p.spring);
      }
    });

    navEl.addEventListener('pointermove', e => {
      const d = _pillDrag; if (!d || e.pointerId !== d.id) return;
      d.trail.push({ x: e.clientX, t: e.timeStamp });
      while (d.trail.length > 2 && e.timeStamp - d.trail[0].t > 100) d.trail.shift();
      if (!d.on) {
        const dx = e.clientX - d.x0, dy = e.clientY - d.y0;
        if (Math.abs(dx) < 6 || Math.abs(dx) < Math.abs(dy)) return;
        d.on = true;
        try { navEl.setPointerCapture(d.id); } catch {}
        navEl.classList.add('ios-nav-dragging');
        $('ios-pill')?.classList.add('ios-pill-lifted');
        _pill.tl = 1;
      }
      e.preventDefault();
      _pillFollow(navEl, e.clientX);
    });

    const finish = (e, cancelled) => {
      const d = _pillDrag; if (!d || e.pointerId !== d.id) return;
      _pillDrag = null;
      if (!d.on) {
        // A tap: the click does the rest. A quick tap still shows its bulge
        // for a moment before the lens settles back.
        const unpress = () => {
          _pill.tl = 0;
          $('ios-pill')?.classList.remove('ios-pill-lifted');
          _pillGo(_pill.tx, _pill.tw, _pill.spring);
        };
        const held = d.pressAt != null ? e.timeStamp - d.pressAt : Infinity;
        if (held < 140 && !_RM.matches) _pillUnpress = setTimeout(unpress, 140 - held);
        else unpress();
        return;
      }
      _pill.tl = 0;
      navEl.classList.remove('ios-nav-dragging');
      $('ios-pill')?.classList.remove('ios-pill-lifted');
      d.peek?.el.classList.remove('ios-peek');
      _pillEatClick = true;
      setTimeout(() => { _pillEatClick = false; }, 0);
      // The hand's speed over the last tenth of a second. A hand that
      // stopped before letting go throws nothing.
      d.trail.push({ x: e.clientX, t: e.timeStamp });
      const recent = d.trail.filter(s => e.timeStamp - s.t <= 100);
      const a = recent[0], b = recent[recent.length - 1];
      const v = recent.length > 1 && b.t > a.t ? (b.x - a.x) / ((b.t - a.t) / 1000) : 0;
      const current = navEl.querySelector('.ios-item.active');
      let to = null;
      if (!cancelled) {
        // Where a throw would come to rest, as iOS projects a flick, so a
        // quick flick carries on past the tab under the hand.
        const c = _pill.x + _pill.w / 2 + Math.max(-2500, Math.min(2500, v)) * 0.1;
        to = _pillNearest(d.tabs, c);
      }
      _pill.vx = _RM.matches ? 0 : Math.max(-3000, Math.min(3000, v));
      _pill.thrown = true;
      if (!to || to.el === current) { if (current) movePill(current); return; }
      if (to.el.dataset.page === 'more') {
        // The More sheet opens over the bar; the pill goes home under it.
        if (current) movePill(current);
        openMoreSheet();
        return;
      }
      window.Feel?.touch('light');
      nav(to.el.dataset.page, to.el);
    };
    navEl.addEventListener('pointerup', e => finish(e, false));
    navEl.addEventListener('pointercancel', e => finish(e, true));
    // Only the bar losing the pointer counts: a touch starts captured by its
    // button, which lets go (and says so) when the bar takes it over.
    navEl.addEventListener('lostpointercapture', e => { if (e.target === navEl && _pillDrag?.on) finish(e, false); });

    // The click after a drag is the end of the drag, not a tap on a tab.
    navEl.addEventListener('click', e => {
      if (_pillEatClick) { _pillEatClick = false; e.preventDefault(); e.stopPropagation(); }
    }, true);

    // Arrow keys walk the tabs, as in a tab bar on the Mac.
    navEl.addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const items = [...navEl.querySelectorAll('.ios-item')];
      const i = items.indexOf(document.activeElement);
      if (i < 0) return;
      const next = items[i + (e.key === 'ArrowRight' ? 1 : -1)];
      if (next) { e.preventDefault(); next.focus(); }
    });

    // The bar changes width with the window on a phone; the pill follows.
    window.addEventListener('resize', () => {
      const cur = navEl.querySelector('.ios-item.active');
      if (cur && !_pillDrag) movePill(cur, true);
    });
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
    _bindPillDrag(navEl);
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
    const ring = () => { if (!window.Feel?.alarm()) _beep(); };
    ring();
    _alarmInterval = setInterval(ring, 1400);
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
    // Every other page is hidden now. The old page used to be hidden from a
    // timer, which could fire after the new page was shown: tapping the tab
    // you were already on then hid it and left the screen blank.
    const target = 'page-' + pageId;
    let changed = false;
    document.querySelectorAll('.page').forEach(p => {
      if (p.id === target) return;
      if (p.classList.contains('active')) changed = true;
      p.classList.remove('active');
      p.style.opacity = '';
    });
    // On a phone the document itself scrolls; a new page starts at its top.
    if (changed && _isPhone()) window.scrollTo(0, 0);
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
    _syncFeedAudio();
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
    const tok = _token;
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
    let opened = false;
    ws.onopen = () => { opened = true; _wsRetryDelay = 3000; _syncPendingEvents(); };
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
      // Refused before it opened (the server answers an unknown session with
      // a failed handshake, which a browser reports as a plain 1006), or closed
      // with 4001: the session has probably expired. Ask once; api() then
      // refreshes the token, or signs out. Either way try again afterwards:
      // if the server was only restarting, the retry is what reconnects, and
      // after a sign-out _scheduleWsRetry does nothing.
      if (!opened || (ev && ev.code === 4001)) {
        const again = () => _scheduleWsRetry();
        api('GET', '/api/session').then(again, again);
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
    _syncFeedAudio();
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
  // One day at a time, read from /api/history: every day the logs on disk
  // hold, not only the last 50 lines of the state push (which is all this card
  // had, so yesterday was always "No activity"). The push still adds new lines
  // as they happen while today is shown.
  let _timelineItems = [];      // the chosen day, newest first
  let _timelineMore = false;    // older lines of that day are on disk
  let _timelineAuto = true;     // following today (not a day someone picked)
  let _timelineDays = [];       // days with lines, newest first
  let _timelineReq = 0;
  let _timelineSig = '';

  const _pad2 = n => String(n).padStart(2, '0');
  // The device stamps lines in local time; toISOString() is UTC, which in
  // India put the picker on yesterday until 05:30.
  function _localDay(d = new Date()) {
    return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`;
  }
  function _tlKey(i) { return i.time + '|' + i.text; }
  // A presence check that found nothing new (ROUTINE in garuda_routes/logs.py):
  // the server leaves these out of the card, and so does the live merge.
  const _ROUTINE = /^\[PRESENCE\] (?:Match|No match) [\u2014\u2013-] \d+ active ARP entries$/;

  // The filter chips: [kind, label, dot]. The server sorts a line into the same
  // kinds (group() in garuda_core/log_history.py) when a filter is asked of it.
  const _TL_KINDS = [['alert', 'Alerts', 'danger'], ['warning', 'Warnings', 'warn'],
    ['watch', 'Watch', 'watch'], ['presence', 'Presence', 'presence'],
    ['mode', 'Modes', 'mode'], ['system', 'System', '']];
  const _TL_DOT = Object.fromEntries(_TL_KINDS.map(k => [k[0], k[2]]));
  let _timelineKinds = new Set();   // none chosen: everything

  function _tlGroup(text) {
    if (text.startsWith('[TAMPER] Dead-man switch disabled')) return 'system';
    if (/\[(?:TAMPER|DANGER)\]|\balert\b/i.test(text)) return 'alert';
    if (text.includes('[WATCH]')) return 'watch';
    if (text.includes('[PRESENCE]') || text.includes('[OWNER]')) return 'presence';
    if (/\[MODE\]|^mode \w+ set to\b|\bmode (?:on|off)\b/i.test(text)) return 'mode';
    if (/\[NETWORK\]|\bfail|\berror\b|\blost\b|\btimed out\b/i.test(text)) return 'warning';
    return 'system';
  }
  function _tlItem(time, text) {
    const kind = _tlGroup(text);
    return { time, text, kind, type: _TL_DOT[kind], dateKey: time.split(' ')[0] || '' };
  }
  function _tlFromLine(entry) {
    const m = entry.match(/^\[([\d\-: ]+)\]\s*/);
    return _tlItem(m ? m[1].trim() : '', m ? entry.slice(m[0].length) : entry);
  }

  function _initTimeline() {
    const picker = $('activity-date');
    _timelineAuto = true;
    _timelineKinds = new Set((_lsGet('garuda-activity-kinds') || '').split(',')
      .filter(k => k in _TL_DOT));
    _renderTimelineFilters();
    if (picker) {
      picker.value = picker.max = _localDay();
      picker.onchange = () => { _timelineAuto = picker.value === _localDay(); _loadTimeline(); };
    }
    _loadTimeline();
    _loadTimelineDays();
  }

  async function _loadTimelineDays() {
    try {
      const d = await api('GET', '/api/history/days?source=system');
      _timelineDays = (d.days || []).map(x => x.date);
      const picker = $('activity-date');
      if (picker && _timelineDays.length) picker.min = _timelineDays[_timelineDays.length - 1];
      _renderTimeline();
    } catch (_) {}
  }

  function _renderTimelineFilters() {
    const el = $('activity-filters');
    if (!el) return;
    const chip = (kind, label, dot, on) =>
      `<button type="button" class="tl-chip" aria-pressed="${on}" data-click="G.toggleActivityKind" data-args='["${kind}"]'>${
        dot === null ? '' : `<span class="tl-dot ${dot}"></span>`}${label}</button>`;
    el.innerHTML = chip('', 'All', null, !_timelineKinds.size) +
      _TL_KINDS.map(([kind, label, dot]) => chip(kind, label, dot, _timelineKinds.has(kind))).join('');
  }

  // A chip adds or removes its kind; All, or every kind chosen, is no filter.
  function toggleActivityKind(kind) {
    if (!kind || !_TL_DOT.hasOwnProperty(kind)) _timelineKinds.clear();
    else if (!_timelineKinds.delete(kind)) _timelineKinds.add(kind);
    if (_timelineKinds.size === _TL_KINDS.length) _timelineKinds.clear();
    _lsSet('garuda-activity-kinds', [..._timelineKinds].join(','));
    _renderTimelineFilters();
    _loadTimeline();
  }

  // Step to the previous (-1) or next (+1) day that has anything in it.
  function stepActivityDay(dir) {
    const picker = $('activity-date');
    if (!picker) return;
    const cur = picker.value || _localDay();
    const today = _localDay();
    const days = _timelineDays.filter(d => d <= today);
    const next = dir < 0 ? days.find(d => d < cur) : [...days].reverse().find(d => d > cur);
    if (!next) return;
    picker.value = next;
    _timelineAuto = next === today;
    _loadTimeline();
  }

  async function _loadTimeline(older) {
    const day = $('activity-date')?.value || _localDay();
    const req = ++_timelineReq;
    const q = new URLSearchParams({ sources: 'system', date: day, limit: '150' });
    if (older && _timelineItems.length) q.set('before', _timelineItems[_timelineItems.length - 1].time);
    if (_timelineKinds.size) q.set('kind', [..._timelineKinds].join(','));
    if (!older) { _timelineItems = []; _timelineMore = false; _renderTimeline(true); }
    try {
      const d = await api('GET', '/api/history?' + q.toString());
      if (req !== _timelineReq) return;           // another day was picked meanwhile
      const have = new Set(_timelineItems.map(_tlKey));
      const items = (d.entries || []).map(e => _tlItem(e.ts, e.text)).reverse()
        .filter(i => !have.has(_tlKey(i)));
      _timelineItems = older ? _timelineItems.concat(items) : items;
      _timelineMore = (d.omitted_before || 0) > 0;
    } catch (_) {
      if (req !== _timelineReq) return;
    }
    _timelineSig = '';                          // let the next push add what is newer
    _renderTimeline();
  }

  function loadOlderActivity() { _loadTimeline(true); }

  function _updateTimeline(s) {
    const log = s.system_log || [];
    const picker = $('activity-date');
    // Past midnight, a card that was following today follows the new day.
    if (_timelineAuto && picker && picker.value !== _localDay()) {
      picker.value = picker.max = _localDay();
      _loadTimeline();
      _loadTimelineDays();
      return;
    }
    const sig = _logSig(log);
    if (sig === _timelineSig) return;         // nothing new: leave the list alone
    _timelineSig = sig;
    const day = picker?.value || _localDay();
    const have = new Set(_timelineItems.map(_tlKey));
    const fresh = log.map(_tlFromLine)
      .filter(i => i.dateKey === day && !_ROUTINE.test(i.text) && !have.has(_tlKey(i)));
    if (!fresh.length) return;
    _timelineItems = fresh.reverse().concat(_timelineItems)
      .sort((a, b) => (a.time < b.time ? 1 : a.time > b.time ? -1 : 0));
    if (!_timelineDays.includes(day)) _timelineDays.unshift(day);
    _renderTimeline();
  }
  function _logSig(log) { return log.length + '|' + (log.length ? log[log.length - 1] : ''); }

  function _renderTimeline(loading) {
    const el = $('activity-timeline');
    if (!el) return;
    const picker = $('activity-date');
    const day = picker?.value || _localDay();
    const today = _localDay();
    const prev = $('activity-prev'), next = $('activity-next');
    if (prev) prev.disabled = !_timelineDays.some(d => d < day);
    if (next) next.disabled = day >= today;
    // Also sorted here: lines from the state push, and a server without the filter.
    const shown = _timelineKinds.size
      ? _timelineItems.filter(i => _timelineKinds.has(i.kind)) : _timelineItems;
    if (!shown.length) {
      const when = day === today ? 'today' : 'on ' + esc(day);
      const none = _timelineKinds.size
        ? `Nothing of this kind ${when}` + (_timelineMore ? ' in the latest lines' : '')
        : (day === today ? 'No activity yet today' : 'No activity recorded on ' + esc(day));
      el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">\u25CB</div><span>${
        loading ? 'Loading\u2026' : none}</span></div>` + (loading ? '' :
        _timelineMore ? '<button class="btn btn-ghost btn-sm tl-older" data-click="G.loadOlderActivity">Show earlier</button>'
        : _timelineKinds.size ? '<button class="btn btn-ghost btn-sm tl-older" data-click="G.toggleActivityKind">Show everything</button>' : '');
      return;
    }
    el.innerHTML = shown.map(item =>
      `<div class="timeline-item">
        <div class="tl-dot ${esc(item.type)}"></div>
        <div class="tl-body">
          <div class="tl-title">${esc(item.text)}</div>
          <div class="tl-time">${esc(item.time)}</div>
        </div>
      </div>`
    ).join('') + (_timelineMore
      ? '<button class="btn btn-ghost btn-sm tl-older" data-click="G.loadOlderActivity">Show earlier</button>'
      : '');
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

    // Log badge counts (the Logs page counts what it shows once it has loaded)
    if (!_logsLoaded) {
      setText('log-count-system', (s.system_log || []).length || 0);
      setText('log-count-detection', s.detection_log_count || 0);
      setText('log-count-presence', s.presence_log_count || 0);
      setText('log-count-voice', ((s.voice_log || []).length + (s.voice_responses || []).length) || 0);
    }


    // Security health panel
    _updateSecHealth(s);

    // New lines into the Logs page, if it is open on the newest ones.
    if (_logsLoaded) _mergeLiveLogs(s.system_log || []);
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
      const loc = cfg.location || {};
      if ($('loc-lat')) $('loc-lat').value = loc.lat ?? '';
      if ($('loc-lon')) $('loc-lon').value = loc.lon ?? '';
      if ($('loc-sun')) $('loc-sun').textContent = cfg.sun && cfg.sun.sunrise
        ? `Today: sunrise ${cfg.sun.sunrise}, sunset ${cfg.sun.sunset}` : '';
    } catch(e) {}
    loadDevices();
    loadMasterKeys();
  }

  function useHereForLocation() {
    if (!navigator.geolocation) { showToast('This browser cannot share its location.', 'error'); return; }
    navigator.geolocation.getCurrentPosition(pos => {
      $('loc-lat').value = pos.coords.latitude.toFixed(4);
      $('loc-lon').value = pos.coords.longitude.toFixed(4);
      showToast('Location filled in. Save to keep it.', 'success');
    }, () => showToast('Location was not shared.', 'error'), { timeout: 10000 });
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
      const latRaw = (($('loc-lat') || {}).value || '').trim();
      const lonRaw = (($('loc-lon') || {}).value || '').trim();
      if (latRaw && lonRaw) {
        payload.location_lat = parseFloat(latRaw);
        payload.location_lon = parseFloat(lonRaw);
      } else if (!latRaw && !lonRaw && $('loc-lat')) {
        payload.location_clear = true;
      } else {
        showToast('Fill in both latitude and longitude, or neither.', 'error');
        return;
      }
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
      window.Feel?.play('unlock');
      $('logs-gate')?.classList.add('hidden');
      if ($('lg-key')) $('lg-key').value = '';
      fetchAndRenderLogs();
    } catch(e) {
      if (errEl) { errEl.textContent = extractError(e); errEl.classList.remove('hidden'); }
    }
  }

  // ── Logs page ───────────────────────────────────────────────
  // Every tab reads /api/history: the newest 500 lines across every file kept
  // on disk, or one chosen day, or whatever matches the filter on any day; and
  // "Show earlier" pages further back. It used to show the 500 lines held in
  // memory, and two seconds later the 50 of the state push, so the filter
  // searched only those 50.
  const _LOG_KINDS = ['system', 'detection', 'presence', 'voice'];
  const _LOG_PANES = { system: 'a-syslog', detection: 'a-detlog', presence: 'a-preslog', voice: 'a-vlog' };
  let _logLines = { system: [], detection: [], voice: [], presence: [] };   // {ts, text}, oldest first
  let _logMore = {};
  let _logTab = 'system';
  let _logsLoaded = false;
  let _logReq = 0;
  let _logQTimer = null;

  function _logFilters() { return { day: val('log-date') || '', q: (val('log-q') || '').trim() }; }
  const _logKey = e => e.ts + '|' + e.text;

  async function _fetchLogKind(kind, olderThan) {
    const { day, q } = _logFilters();
    // The Logs page is the raw log: the routine presence checks too.
    const p = new URLSearchParams({ sources: kind, limit: '500', routine: 'true' });
    if (day) p.set('date', day);
    if (q) p.set('q', q);
    if (olderThan) p.set('before', olderThan);
    const d = await api('GET', '/api/history?' + p.toString());
    if (d.not_searched && d.not_searched[kind]) throw { detail: 'Master key required to view logs.' };
    return { lines: d.entries || [], more: (d.omitted_before || 0) > 0 };
  }

  async function fetchAndRenderLogs() {
    const req = ++_logReq;
    try {
      const got = await Promise.all(_LOG_KINDS.map(k => _fetchLogKind(k)));
      if (req !== _logReq) return;              // the filter changed meanwhile
      _LOG_KINDS.forEach((k, i) => { _logLines[k] = got[i].lines; _logMore[k] = got[i].more; });
      _logsLoaded = true;
      renderLogs();
      clearTimeout(_logLiveTimer);
      _logLiveTimer = setTimeout(_refreshLogTab, 10000);
    } catch(e) {
      if (req !== _logReq) return;
      // 403 means logs not unlocked — re-show gate
      if (e && (e.detail || '').toString().includes('Master key')) {
        _logsUnlocked = false;
        $('logs-gate')?.classList.remove('hidden');
        return;
      }
      _fetchLogsFromMemory();                   // a server without /api/history
    }
  }

  async function _fetchLogsFromMemory() {
    try {
      const data = await api('GET', '/api/logs');
      const parse = l => { const i = _tlFromLine(l); return { ts: i.time, text: i.text }; };
      _logLines = {
        system: (data.system_log || []).map(parse),
        detection: (data.detection_log || []).map(parse),
        voice: [...(data.voice_log || []), ...(data.voice_responses || [])].map(parse),
        presence: (data.presence_log || []).map(e => ({ ts: e.ts, text: `${e.device || 'Unknown'} ${e.event} (${e.mac || 'no mac'})` })),
      };
      _logMore = {};
      _logsLoaded = true;
      renderLogs();
    } catch(e) {
      if (e && (e.detail || '').toString().includes('Master key')) {
        _logsUnlocked = false;
        $('logs-gate')?.classList.remove('hidden');
      }
    }
  }

  async function loadOlderLogs() {
    const kind = _logTab, cur = _logLines[kind];
    if (!cur.length) return;
    const req = _logReq;
    try {
      const got = await _fetchLogKind(kind, cur[0].ts);
      if (req !== _logReq) return;
      const have = new Set(cur.map(_logKey));
      _logLines[kind] = got.lines.filter(e => !have.has(_logKey(e))).concat(cur);
      _logMore[kind] = got.more;
      _renderLogPane(kind, 'older');
    } catch(_) { showToast('Could not read earlier lines.', 'error'); }
  }

  // The open tab, read again while the page shows the newest lines: the state
  // push carries only the system log, so Voice, Detection and Presence stood
  // still until the page was opened again.
  let _logLiveTimer = null;
  async function _refreshLogTab() {
    clearTimeout(_logLiveTimer);
    if (_currentPage !== 'a-logs' || !_logsUnlocked || !_logsLoaded) return;
    const kind = _logTab, { day, q } = _logFilters();
    if (!q && (!day || day === _localDay()) && !document.hidden) {
      const req = _logReq;
      try {
        const got = await _fetchLogKind(kind);
        if (req === _logReq && kind === _logTab) {
          const cur = _logLines[kind], have = new Set(cur.map(_logKey));
          const fresh = got.lines.filter(e => !have.has(_logKey(e)));
          // A line that grew (a reply joins its question) is replaced, not repeated.
          const keep = cur.filter(e => !got.lines.some(g => g.ts === e.ts && g.text !== e.text
                                                                  && g.text.startsWith(e.text)));
          if (fresh.length) {
            _logLines[kind] = keep.concat(fresh).sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
            _renderLogPane(kind, 'live');
          }
        }
      } catch (_) { /* the next tick tries again */ }
    }
    _logLiveTimer = setTimeout(_refreshLogTab, 10000);
  }

  // New lines from the state push, while the page shows the newest (no past day, no filter).
  function _mergeLiveLogs(lines) {
    const { day, q } = _logFilters();
    if (q || (day && day !== _localDay())) return;
    const cur = _logLines.system;
    const have = new Set(cur.map(_logKey));
    const fresh = lines.map(_tlFromLine).map(i => ({ ts: i.time, text: i.text }))
      .filter(e => !have.has(_logKey(e)) && (!day || e.ts.startsWith(day)));
    if (!fresh.length) return;
    _logLines.system = cur.concat(fresh);
    _renderLogPane('system', 'live');
  }

  async function downloadFullLog() {
    const base = getBackend();
    const tok  = _token;
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

  // how: 'older' keeps the reader's place as earlier lines go on top; 'live'
  // follows new lines only for a reader already at the bottom.
  function _renderLogPane(kind, how) {
    const el = $(_LOG_PANES[kind]);
    if (!el) return;
    const { day, q } = _logFilters();
    const lines = _logLines[kind] || [];
    const fromBottom = el.scrollHeight - el.scrollTop;
    const atBottom = fromBottom - el.clientHeight < 8;
    const fmt = e => kind === 'presence' ? `${e.ts}  ${e.text}` : `[${e.ts}] ${e.text}`;
    if (!lines.length) {
      el.textContent = q ? `Nothing matches "${q}"${day ? ' on ' + day : ''}.`
        : day ? `Nothing recorded on ${day}.` : 'Nothing recorded yet.';
    } else if (kind === 'voice') {
      el.innerHTML = lines.map(e => `<div class="log-line">${esc(fmt(e))}</div>`).join('');
    } else {
      el.textContent = lines.map(fmt).join('\n');
    }
    if (how === 'older') el.scrollTop = el.scrollHeight - fromBottom;
    else if (how !== 'live' || atBottom) el.scrollTop = el.scrollHeight;
    setText(`log-count-${kind}`, lines.length + (_logMore[kind] ? '+' : ''));
    if (kind === _logTab) _syncLogOlder();
  }

  function _syncLogOlder() {
    const b = $('log-older');
    if (b) b.disabled = !_logMore[_logTab];
  }

  function renderLogs() { _LOG_KINDS.forEach(k => _renderLogPane(k)); }

  // Typing searches every day on disk, once the typing pauses.
  function filterLogs() {
    if (!_logsUnlocked) return;
    clearTimeout(_logQTimer);
    _logQTimer = setTimeout(fetchAndRenderLogs, 350);
  }

  // ── Log tab switching ──────────────────────────────────────
  function switchLogTab(tab) {
    document.querySelectorAll('.log-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.log-pane').forEach(p => p.classList.remove('active'));
    const tabEl = document.querySelector(`.log-tab[data-log="${tab}"]`);
    const paneEl = document.getElementById(`log-pane-${tab}`);
    if (tabEl) tabEl.classList.add('active');
    if (paneEl) paneEl.classList.add('active');
    _logTab = tab;
    _syncLogOlder();
    _refreshLogTab();                         // what was added since the page opened
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

  // What the open tab shows, as shown (a day, a search, or the newest lines).
  function exportLogs() {
    const fmt = e => `[${e.ts}] ${e.text}`;
    const blob = new Blob([(_logLines[_logTab] || []).map(fmt).join('\n')], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `garuda-${_logTab}-log-${val('log-date') || _localDay()}.txt`;
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
          ${keys.length > 1 ? `<button class="mk-item-del" data-click="G.deleteMasterKey" data-args="[${i}]" title="Delete">×</button>` : ''}
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
    window.Feel?.play('error');
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
    if (_token) headers['X-Garuda-Token'] = _token;
    const opts = { method, headers, credentials: base ? 'omit' : 'include' };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(fullUrl, opts);
    let d;
    try { d = await r.json(); } catch(_) { d = { detail: r.statusText || `HTTP ${r.status}` }; }
    if (!r.ok) {
      d.status = r.status;
      // On 401, attempt one silent token refresh before giving up
      if (r.status === 401 && !_isRetry && !_loggingOut && !['/api/refresh', '/api/login', '/api/logout'].includes(url)) {
        try {
          if (await _refreshAccess()) return api(method, url, body, true);   // retry once with the new token
        } catch (_) {}
        // Refresh failed — session is gone, force re-login (no question
        // asked: there is nothing left to stay signed in to).
        if (_session) await _doLogout();
      }
      throw d;
    }
    return d;
  }

  // ── Scroll fades ──────────────────────────────────────────
  // Every box that scrolls fades its content out at the sides that have more
  // to show (style.css: "Scroll fades"). This keeps the marks current.
  const FADE_Y = '#main, .nx-log, .nx-info-body, .console, .det-timeline, .det-feed, .activity-feed, '
    + '.docs-body, .modal, .info-pane, .timeline-list, .activity-timeline-wrap, .fb-inbox-list, .fb-panel, '
    + '.ha-activity, .more-panel, .sidebar-left, .sidebar-right, .admin-page, .page-inner, .dash-layout, .login-center';
  const FADE_X = '.docs-tabs, .log-tabs, .t-wrap, #ios-nav, #heatmap, .fc-wrap, .docs-section pre';
  const _fadeSeen = new WeakSet();
  let _fadeQueued = false, _fadeResize = null;

  function _fadeSync(el) {
    const y = el.classList.contains('fade-y');
    const pos = y ? el.scrollTop : el.scrollLeft;
    const max = y ? el.scrollHeight - el.clientHeight : el.scrollWidth - el.clientWidth;
    const before = pos > 1, after = max > 1 && pos < max - 1;
    el.toggleAttribute(y ? 'data-ft' : 'data-fl', before);
    el.toggleAttribute(y ? 'data-fb' : 'data-fr', after);
    if (before || after) el.classList.add('fade-on');
    else if (el.classList.contains('fade-on')) {
      // Kept until the fade has run out, so the last edge does not snap sharp.
      clearTimeout(el._fadeOff);
      el._fadeOff = setTimeout(() => {
        if (!el.hasAttribute('data-ft') && !el.hasAttribute('data-fb')
            && !el.hasAttribute('data-fl') && !el.hasAttribute('data-fr')) el.classList.remove('fade-on');
      }, 260);
    }
  }

  function _fadeAttach(el, axis) {
    if (_fadeSeen.has(el)) return _fadeSync(el);
    _fadeSeen.add(el);
    el.classList.add(axis);
    el.addEventListener('scroll', () => _fadeSync(el), { passive: true });
    if (_fadeResize) _fadeResize.observe(el);
    _fadeSync(el);
  }

  function _fadeScan() {
    _fadeQueued = false;
    document.querySelectorAll(FADE_Y).forEach(el => _fadeAttach(el, 'fade-y'));
    document.querySelectorAll(FADE_X).forEach(el => _fadeAttach(el, 'fade-x'));
  }

  function _initFades() {
    if (!window.CSS || !CSS.supports || !(CSS.supports('mask-image', 'none') || CSS.supports('-webkit-mask-image', 'none'))) return;
    if (window.ResizeObserver) _fadeResize = new ResizeObserver(entries => entries.forEach(e => _fadeSync(e.target)));
    _fadeScan();
    // Content arrives and leaves all the time (logs, lists, a new message):
    // look again shortly after the page changes, at most a few times a second.
    new MutationObserver(() => {
      if (_fadeQueued) return;
      _fadeQueued = true;
      setTimeout(_fadeScan, 180);
    }).observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', () => { if (!_fadeQueued) { _fadeQueued = true; setTimeout(_fadeScan, 180); } });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _initFades);
  else _initFades();

  // A POST whose answer arrives in pieces (server-sent events): onEvent is
  // given each one as it comes. Used by Narada to show its steps as it works.
  async function apiStream(url, body, onEvent) {
    const base = getBackend();
    const fullUrl = base ? base.replace(/\/$/, '') + url : url;
    const headers = { 'Content-Type': 'application/json' };
    if (_token) headers['X-Garuda-Token'] = _token;
    const r = await fetch(fullUrl, { method: 'POST', headers, credentials: base ? 'omit' : 'include', body: JSON.stringify(body) });
    if (!r.ok || !r.body) {
      let d;
      try { d = await r.json(); } catch (_) { d = { detail: r.statusText || `HTTP ${r.status}` }; }
      d.status = r.status;
      throw d;
    }
    const reader = r.body.getReader(), decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let at;
      while ((at = buf.indexOf('\n\n')) !== -1) {
        const chunk = buf.slice(0, at);
        buf = buf.slice(at + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          let event = null;
          try { event = JSON.parse(line.slice(6)); } catch (_) {}
          if (event) onEvent(event);
        }
      }
    }
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
    toggleCamera, toggleFeedSound, takeSnapshot, toggleClip, openDocs,
    loadEmailCfg, saveEmail, testEmail,
    loadSysCfg, togglePrivacy, toggleNightPresence, useHereForLocation, saveSettings,
    filterLogs, exportLogs, downloadFullLog, loadOlderLogs, onLogDate: fetchAndRenderLogs,
    loadOlderActivity, stepActivityDay, toggleActivityKind,
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
    _apiStream: apiStream,
    _base: () => (getBackend() || '').replace(/\/$/, ''),
    _authQuery: () => (_token ? 'token=' + encodeURIComponent(_token) : ''),
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
  let _fbTab    = 'send';     // an admin's tab: 'send' or 'inbox'

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
        '<button class="fb-inbox-refresh" data-click="G.fbLoadInbox" title="Refresh">↻</button>' +
      '</div>' +
      '<div class="fb-cats" id="fb-inbox-filters" style="margin-bottom:8px">' +
        '<span class="fb-cat sel" data-filter="all" data-click="G.fbFilterInbox" data-args="[{&quot;$&quot;: &quot;this&quot;}]">All</span>' +
        '<span class="fb-cat" data-filter="bug" data-click="G.fbFilterInbox" data-args="[{&quot;$&quot;: &quot;this&quot;}]">Bug</span>' +
        '<span class="fb-cat" data-filter="feature" data-click="G.fbFilterInbox" data-args="[{&quot;$&quot;: &quot;this&quot;}]">Feature</span>' +
        '<span class="fb-cat" data-filter="general" data-click="G.fbFilterInbox" data-args="[{&quot;$&quot;: &quot;this&quot;}]">General</span>' +
        '<span class="fb-cat" data-filter="other" data-click="G.fbFilterInbox" data-args="[{&quot;$&quot;: &quot;this&quot;}]">Other</span>' +
      '</div>' +
      '<div class="fb-inbox-list" id="fb-inbox-list">' +
        '<div class="fb-inbox-empty">No feedback yet.</div>' +
      '</div>';
    panel.appendChild(d);
  }

  // ── Show correct view for current role ────────────────────
  // Everyone can write. An admin also has the inbox, as a second tab: an
  // admin used to get only the inbox, so the person most likely to have
  // something to say about the house had nowhere to say it.
  function _applyRole() {
    const tabBar = document.getElementById('fb-tabs');
    if (!tabBar) return;
    if (_isAdmin()) {
      _ensureInbox();
      if (!tabBar.children.length) {
        tabBar.innerHTML =
          '<button type="button" class="fb-tab" role="tab" data-tab="send" data-click="G.fbSwitchTab" data-args=\'["send"]\'>Write</button>' +
          '<button type="button" class="fb-tab" role="tab" data-tab="inbox" data-click="G.fbSwitchTab" data-args=\'["inbox"]\'>Inbox<span class="fb-tab-count" id="fb-tab-count"></span></button>';
      }
      tabBar.style.display = '';
    } else {
      tabBar.innerHTML = '';
      tabBar.style.display = 'none';
    }
    G.fbSwitchTab(_isAdmin() ? _fbTab : 'send');
  }

  G.fbSwitchTab = function(tab) {
    _fbTab = (tab === 'inbox' && _isAdmin()) ? 'inbox' : 'send';
    const sendTab = document.getElementById('fb-tab-send');
    const inbox = document.getElementById('fb-tab-inbox');
    if (sendTab) sendTab.style.display = _fbTab === 'send' ? '' : 'none';
    if (inbox) inbox.style.display = _fbTab === 'inbox' ? '' : 'none';
    document.querySelectorAll('#fb-tabs .fb-tab').forEach(t => {
      const on = t.dataset.tab === _fbTab;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', String(on));
    });
    const title = document.getElementById('fb-title');
    if (title) title.textContent = _fbTab === 'inbox' ? 'Feedback inbox' : 'Send feedback';
    if (_fbTab === 'inbox' && _fbOpen) G.fbLoadInbox();
  };

  function _setOpen(open) {
    _fbOpen = open;
    document.getElementById('fb-panel').classList.toggle('open', open);
    document.getElementById('fb-bubble').classList.toggle('is-open', open);
  }
  G.closeFeedback = function() { _setOpen(false); };

  // ── Open / close ──────────────────────────────────────────
  G.toggleFeedback = function() {
    _setOpen(!_fbOpen);
    if (!_fbOpen) return;
    _applyRole();
    if (_isAdmin()) G.fbLoadInbox();          // keeps the Inbox tab's count current
    // A keyboard is there to type; on a phone, focusing at once would throw
    // the on-screen keyboard over the form before it has been read.
    if (_fbTab === 'send' && !matchMedia('(pointer: coarse)').matches) {
      const m = document.getElementById('fb-msg');
      if (m) m.focus();
    }
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && _fbOpen) _setOpen(false); });

  // Close on outside click
  document.addEventListener('pointerdown', e => {
    if (!_fbOpen) return;
    const wrap = document.getElementById('fb-wrap');
    // The More sheet's own row opens the panel; a press there is not "outside".
    if (wrap && !wrap.contains(e.target) && !e.target.closest('.more-sheet, .more-panel')) _setOpen(false);
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
    _fbRating = 0; _fbCat = 'general'; _fbInbox = []; _fbFilter = 'all'; _fbTab = 'send';
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
    const count = document.getElementById('fb-tab-count');
    if (count) count.textContent = total ? ' ' + total : '';
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
      if (_isAdmin()) G.fbLoadInbox();
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

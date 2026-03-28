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
  let _allLogs      = [];
  let _presenceLogs = [];
  let _cfg          = {};
  let _logsUnlocked = false;
  let _lastAlertState = false;
  let _uptimeBase = 0;          // seconds from backend
  let _uptimeReceivedAt = 0;    // Date.now() when received
  let _uptimeInterval = null;   // interval ID — cleared on logout to prevent accumulation
  let _chatInputController = null; // AbortController for chat input listeners

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
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span>${message}</span><button class="toast-dismiss" onclick="this.parentElement.classList.add('removing');setTimeout(()=>this.parentElement.remove(),200)">&times;</button>`;
    container.appendChild(toast);
    if (container.children.length > 3) container.firstChild.remove();
    setTimeout(() => {
      if (toast.parentElement) {
        toast.classList.add('removing');
        setTimeout(() => toast.remove(), 200);
      }
    }, duration);
  }

  // ── Hardware stats ────────────────────────────────────────
  function _updateHw(s) {
    if (s.cpu_percent != null) setText('hwm-cpu',  Math.round(s.cpu_percent) + '%');
    if (s.ram_percent != null) setText('hwm-ram',  Math.round(s.ram_percent) + '%');
    if (s.cpu_temp    != null) setText('hwm-temp', Math.round(s.cpu_temp) + '\u00b0');
    if (s.inference_fps != null) setText('hwm-fps', Math.round(s.inference_fps));
    if (s.disk_percent != null) setText('hwm-disk', Math.round(s.disk_percent) + '%');

    // Update metric rings
    if (s.cpu_percent != null) updateMetricRing('hw-cpu', s.cpu_percent, 100, '%');
    if (s.ram_percent != null) updateMetricRing('hw-ram', s.ram_percent, 100, '%');
    if (s.cpu_temp != null) updateMetricRing('hw-temp', s.cpu_temp, 85, '\u00b0C');
    if (s.inference_fps != null) updateMetricRing('hw-fps', s.inference_fps, 60, 'fps');
    if (s.disk_percent != null) updateMetricRing('hw-disk', s.disk_percent, 100, '%');
  }

  // ── Hardware metric ring updater ───────────────────────────
  const RING_CIRCUMFERENCE = 2 * Math.PI * 27; // 169.65
  function updateMetricRing(id, value, max, unit) {
    const ring = document.getElementById(id + '-ring');
    const valEl = document.getElementById(id.replace('hw-','hwm-').replace('-tile',''));
    if (!ring) return;
    const pct = Math.min(value / max, 1);
    const offset = RING_CIRCUMFERENCE * (1 - pct);
    ring.style.strokeDashoffset = offset;
    // Color: green < 60%, yellow 60-80%, red > 80%
    const color = pct < 0.6 ? '#34C759' : pct < 0.8 ? '#FF9F0A' : '#FF3B30';
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
        <div class="activity-dot ${item.type}"></div>
        <div class="activity-text">${item.text}</div>
        <div class="activity-time">${item.time}</div>
      </div>`
    ).join('');
  }

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

    // Check remember-me token (user-only, 5-day device token)
    const remRaw = localStorage.getItem('garuda_remember');
    if (remRaw) {
      try {
        const rem = JSON.parse(remRaw);
        if (rem.expires > Date.now() && rem.token) {
          _token = rem.token;
          const session = await api('GET', '/api/session');
          if (session && session.role !== 'admin') {
            _session = session;
            afterLogin();
            return;
          }
        }
      } catch(_) {}
      localStorage.removeItem('garuda_remember');
      _token = null;
    }

    // Try to restore session from previous visit (cookie / garuda_token)
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
    renderHeatmap({});  // render empty heatmap; real data arrives via WS after login
  }

  // ── Login view switcher ───────────────────────────────────
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
      // Dev-only OTP fallback (only shown on localhost)
      if (r && !r.ok && r.bypass_otp && location.hostname === 'localhost') {
        const e2 = $('adm-err-2');
        if (e2) { e2.textContent = 'Email failed. Dev code: ' + r.bypass_otp; e2.classList.remove('hidden'); }
      }
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
      if (res.token && getBackend()) {
        _token = res.token;
        localStorage.setItem('garuda_token', _token);
      }
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
      if (res.token && getBackend()) {
        _token = res.token;
        localStorage.setItem('garuda_token', _token);
      }
      // Store 5-day device token (user only, only if checkbox was ticked)
      if (remember && res.role === 'user' && res.token) {
        localStorage.setItem('garuda_remember', JSON.stringify({
          token: res.token,
          expires: Date.now() + 5 * 24 * 60 * 60 * 1000
        }));
      }
      afterLogin();
    } catch(e) {
      showLoginErr(errEl, extractError(e));
    }
  }

  function afterLogin() {
    $('app').classList.add('logged-in');
    $('hdr-user').textContent = _session.display_name || _session.username;
    buildNav(_session.role);
    nav('dashboard');
    _initChatInput();
    // Live uptime ticker — save ID so it can be cleared on logout
    if (_uptimeInterval) clearInterval(_uptimeInterval);
    _uptimeInterval = setInterval(() => { if (_uptimeReceivedAt) setText('s-uptime', _fmtUptimeLive()); }, 1000);
    // Always reset console visibility first, then show for admin only
    const cw = $('dash-console-wrap');
    if (cw) {
      cw.classList.add('hidden');
      if (_session.role === 'admin') cw.classList.remove('hidden');
    }
    // Set logs unlock state from session (master key login sets this true)
    _logsUnlocked = !!_session.logs_unlocked;
    $('logs-gate')?.classList.add('hidden');
    connectWS();
    if (_session.role === 'admin') loadCfg();
  }

  async function logout() {
    try { await api('POST', '/api/logout', {}); } catch(_) {}
    // Clear uptime interval and chat listeners before resetting state
    if (_uptimeInterval) { clearInterval(_uptimeInterval); _uptimeInterval = null; }
    if (_chatInputController) { _chatInputController.abort(); _chatInputController = null; }
    _session = null; _token = null; _logsUnlocked = false;
    _recentDets = []; _prevAlertActive = false; _lastDetInfo = '';
    localStorage.removeItem('garuda_token');
    localStorage.removeItem('garuda_remember');
    if (_ws) { _ws.close(); _ws = null; }
    $('app').classList.remove('logged-in');
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
      const r = await api('POST', '/api/forgot/send-otp', { username: un });
      if (r.bypass_otp && location.hostname === 'localhost') showEl('fp-msg', `Dev OTP: ${r.bypass_otp}`, false);
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
  // Priority: WebRTC (H.264, lowest latency) → WS binary JPEG (CF Tunnel)
  //            → MJPEG (universal fallback)
  let _activePc = null;   // RTCPeerConnection when WebRTC is active
  let _wsStream = null;   // WebSocket when WS-JPEG stream is active

  function _camSetStatus(txt) {
    const el = $('cam-status-txt');
    if (el) el.textContent = txt;
  }

  function stopCameraStream() {
    const camImg   = $('cam-img');
    const camVideo = $('cam-video');
    if (camImg)   { camImg.src = ''; camImg.style.display = 'none'; }
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

  function startMjpeg() {
    const backend  = getBackend() || '';
    const camImg   = $('cam-img');
    const camOffline = $('cam-offline');
    if (!camImg) return;
    camImg.style.display = 'none';
    if (camOffline) camOffline.style.display = 'flex';
    _camSetStatus('Connecting…');
    camImg.onload = () => {
      camImg.style.display = 'block';
      if (camOffline) camOffline.style.display = 'none';
      _camSetStatus('Live · MJPEG');
    };
    camImg.onerror = () => {
      camImg.style.display = 'none';
      if (camOffline) camOffline.style.display = 'flex';
      _camSetStatus('Unavailable');
    };
    const streamToken = _token ? `&token=${encodeURIComponent(_token)}` : '';
    camImg.src = backend + '/stream?t=' + Date.now() + streamToken;
  }

  function toggleCamera() {
    const overlay = $('camera-overlay');
    if (!overlay) return;
    const isHidden = overlay.classList.contains('hidden');
    if (isHidden) {
      overlay.classList.remove('hidden');
      const camOffline = $('cam-offline');
      if (camOffline) camOffline.style.display = 'flex';
      _camSetStatus('Connecting…');
      // Try WebRTC first if available
      if (typeof RTCPeerConnection !== 'undefined') {
        startWebRTC();
      } else {
        startWsStream();
      }
    } else {
      stopCameraStream();
      overlay.classList.add('hidden');
    }
  }

  // ── Chat ──────────────────────────────────────────────────
  let _chatBusy    = false;
  let _thinkTimer  = null;

  function toggleRateLimitInfo() {
    const bubble = $('chat-ratelimit-bubble');
    const btn    = $('chat-info-btn');
    if (!bubble) return;
    const open = bubble.classList.toggle('open');
    if (btn) btn.classList.toggle('active', open);
  }

  const _THINKING = [
    // Processing thoughts
    "Analyzing Hailo-8L inference pipeline state…",
    "Reviewing YOLOv6n detection confidence scores…",
    "Cross-referencing security event log…",
    "Consulting active mode configuration…",
    "Scanning perimeter alert thresholds…",
    "Correlating IMX708 frame metadata…",
    "Evaluating scissors threat probability matrix…",
    "Syncing with Garuda event database…",
    "Checking WebRTC stream health…",
    "Mapping 1280×720 detection grid…",
    "Processing 5-frame confirmation buffers…",
    "Reviewing GPIO sensor state…",
    "Scanning system_logs for recent patterns…",
    "Verifying detection threshold calibration…",
    // Quotes & project philosophy
    "\"Security is not a product, it's a process.\" — Bruce Schneier",
    "\"The price of liberty is eternal vigilance.\" — Thomas Jefferson",
    "60fps. Every frame a question. Every detection an answer.",
    "Standing watch so you don't have to.",
    "5 consecutive frames to confirm. Certainty over speed.",
    "Threshold: the line between alert and silence.",
    "Narada sees. Narada knows. Narada guards.",
    "Every pixel on the IMX708 tells a story.",
    "Privacy preserved. Threats surfaced.",
    "Hailo-8L: 26 TOPS so the Pi 5 CPU doesn't have to.",
    "One scissors detection is noise. Five is signal.",
    "The best alarm is the one that never cries wolf.",
  ];

  // Simple inline markdown renderer
  function _md(text) {
    const esc = text
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc
      // Fenced code blocks
      .replace(/```([^`]*?)```/gs, '<pre class="chat-code-block"><code>$1</code></pre>')
      // Inline code
      .replace(/`([^`\n]+)`/g, '<code class="chat-inline-code">$1</code>')
      // Bold
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      // Italic
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      // Headers (## / ###) → bold line
      .replace(/^#{1,3} (.+)$/gm, '<span class="chat-heading">$1</span>')
      // Bullet lists
      .replace(/^[-•] (.+)$/gm, '<span class="chat-li">$1</span>')
      // Newlines
      .replace(/\n/g, '<br>');
  }

  function _chatAddUser(text) {
    const box = $('chat-messages');
    if (!box) return;
    const el = document.createElement('div');
    el.className = 'chat-msg user';
    el.innerHTML = `<div class="chat-msg-pill">${_md(text)}</div>`;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
  }

  function _chatAddAssistant() {
    // Returns the body element to stream into
    const box = $('chat-messages');
    if (!box) return null;
    const el = document.createElement('div');
    el.className = 'chat-msg assistant';
    el.innerHTML = `
      <div class="chat-msg-avatar-gradient"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
      <div class="chat-msg-content">
        <div class="chat-msg-body"></div>
      </div>`;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
    return el.querySelector('.chat-msg-body');
  }

  function _showThinking() {
    const box = $('chat-messages');
    if (!box || $('chat-thinking')) return;
    const el = document.createElement('div');
    el.id = 'chat-thinking';
    el.className = 'chat-thinking';
    el.innerHTML = `
      <div class="think-header">
        <span class="think-pulse"></span><span>Thinking</span>
      </div>
      <div class="think-lines" id="think-lines"></div>`;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;

    let idx = Math.floor(Math.random() * _THINKING.length);
    const shown = [];
    function addLine() {
      const lines = $('think-lines');
      if (!lines) return;
      const d = document.createElement('div');
      d.className = 'think-line';
      d.textContent = _THINKING[idx % _THINKING.length];
      idx++;
      lines.appendChild(d);
      shown.push(d);
      requestAnimationFrame(() => d.classList.add('think-line-in'));
      if (shown.length > 3) {
        const old = shown.shift();
        old.classList.add('think-line-out');
        setTimeout(() => old.remove(), 350);
      }
      box.scrollTop = box.scrollHeight;
    }
    addLine();
    _thinkTimer = setInterval(addLine, 850);
  }

  function _hideThinking() {
    clearInterval(_thinkTimer);
    _thinkTimer = null;
    const el = $('chat-thinking');
    if (el) {
      el.classList.add('think-fade-out');
      setTimeout(() => el.remove(), 300);
    }
  }

  function _streamInto(bodyEl, text, done) {
    if (!bodyEl) return;
    bodyEl.innerHTML = _md(text) + (done ? '' : '<span class="chat-cursor">|</span>');
    const box = $('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
  }

  async function sendChat() {
    if (_chatBusy) return;
    const input = $('chat-input');
    const btn   = $('chat-send-btn');
    if (!input) return;
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    input.style.height = '';
    _chatAddUser(msg);
    _chatBusy = true;
    if (btn) btn.disabled = true;
    _showThinking();

    try {
      const res  = await api('POST', '/api/chat', { message: msg });
      const text = res.response || '…';
      _hideThinking();
      const bodyEl = _chatAddAssistant();
      // Typewriter: reveal chars at ~18ms each, then snap remaining on done
      let i = 0;
      function tick() {
        if (!bodyEl) return;
        i = Math.min(i + 3, text.length);
        _streamInto(bodyEl, text.slice(0, i), i === text.length);
        if (i < text.length) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    } catch(e) {
      _hideThinking();
      const bodyEl = _chatAddAssistant();
      if (bodyEl) bodyEl.textContent = 'Connection error — please try again.';
    } finally {
      _chatBusy = false;
      if (btn) btn.disabled = false;
      input.focus();
    }
  }

  function clearChat() {
    const box = $('chat-messages');
    if (!box) return;
    box.innerHTML = `
      <div class="chat-msg assistant">
        <div class="chat-msg-avatar-gradient"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
        <div class="chat-msg-content">
          <div class="chat-msg-body">Chat cleared. How can I help?</div>
        </div>
      </div>`;
  }

  function _initChatInput() {
    const input = $('chat-input');
    if (!input) return;
    // Remove previous listeners via AbortController to prevent accumulation across logins
    if (_chatInputController) _chatInputController.abort();
    _chatInputController = new AbortController();
    const sig = { signal: _chatInputController.signal };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
    }, sig);
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    }, sig);
  }

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
  const _NAV_ICONS = {
    dashboard: `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="1.5" width="6" height="6" rx="1.5"/><rect x="10.5" y="1.5" width="6" height="6" rx="1.5"/><rect x="1.5" y="10.5" width="6" height="6" rx="1.5"/><rect x="10.5" y="10.5" width="6" height="6" rx="1.5"/></svg>`,
    narada:    `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 1.5a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0v-5a3 3 0 0 1 3-3z"/><path d="M3.75 8.25a5.25 5.25 0 0 0 10.5 0"/><line x1="9" y1="13.5" x2="9" y2="16.5"/><line x1="6" y1="16.5" x2="12" y2="16.5"/></svg>`,
    users:     `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="5.5" r="2.5"/><path d="M1.5 15.75a5.5 5.5 0 0 1 11 0"/><path d="M13.5 7.5a2.5 2.5 0 1 1 0-5"/><path d="M16.5 15.75a4 4 0 0 0-3-3.85"/></svg>`,
    email:     `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="3.75" width="15" height="10.5" rx="1.5"/><path d="M1.5 5.25 9 10.5l7.5-5.25"/></svg>`,
    settings:  `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="9" r="2.25"/><path d="M14.7 11.1a1 1 0 0 0 .2 1.1l.05.05a1.21 1.21 0 0 1-1.71 1.71l-.05-.05a1 1 0 0 0-1.1-.2 1 1 0 0 0-.61.92v.14a1.21 1.21 0 0 1-2.42 0v-.07a1 1 0 0 0-.65-.92 1 1 0 0 0-1.1.2l-.05.05a1.21 1.21 0 0 1-1.71-1.71l.05-.05a1 1 0 0 0 .2-1.1 1 1 0 0 0-.92-.61H5.4a1.21 1.21 0 0 1 0-2.42h.07a1 1 0 0 0 .92-.65 1 1 0 0 0-.2-1.1l-.05-.05a1.21 1.21 0 0 1 1.71-1.71l.05.05a1 1 0 0 0 1.1.2h.04a1 1 0 0 0 .61-.92V3.4a1.21 1.21 0 0 1 2.42 0v.07a1 1 0 0 0 .61.92 1 1 0 0 0 1.1-.2l.05-.05a1.21 1.21 0 0 1 1.71 1.71l-.05.05a1 1 0 0 0-.2 1.1v.04a1 1 0 0 0 .92.61h.14a1.21 1.21 0 0 1 0 2.42h-.07a1 1 0 0 0-.92.61z"/></svg>`,
    logs:      `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5h12M3 9h12M3 13.5h7.5"/></svg>`,
    commands:  `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="4.5 6 1.5 9 4.5 12"/><polyline points="13.5 6 16.5 9 13.5 12"/><line x1="7.5" y1="3" x2="10.5" y2="15"/></svg>`,
    emergency: `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 1.5 16.5 16.5H1.5Z"/><line x1="9" y1="7" x2="9" y2="11"/><circle cx="9" cy="13.5" r="0.75" fill="currentColor" stroke="none"/></svg>`,
    chat:      `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15.75 9.75a6.75 6.75 0 0 1-9.45 6.19L2.25 16.5l.56-4.05A6.75 6.75 0 1 1 15.75 9.75z"/></svg>`,
  };

  const _USER_NAV = [
    { page: 'dashboard', label: 'Home',   icon: 'dashboard' },
    { page: 'chat',      label: 'Chat',   icon: 'chat'      },
    { page: 'narada',    label: 'Narada', icon: 'narada'    },
  ];

  const _ADMIN_NAV = [
    { page: 'dashboard',  label: 'Home',     icon: 'dashboard' },
    { page: 'chat',       label: 'Chat',     icon: 'chat'      },
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
    const tx  = ir.left - nr.left + navEl.scrollLeft - ovr;
    const w   = ir.width + ovr * 2;
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

  function buildNav(role) {
    const items = role === 'admin' ? _ADMIN_NAV : _USER_NAV;
    const navEl = $('ios-nav');
    if (!navEl) return;
    navEl.querySelectorAll('.ios-item').forEach(el => el.remove());
    items.forEach(item => {
      const btn = document.createElement('button');
      btn.className = 'ios-item' + (item.danger ? ' ios-danger' : '');
      btn.innerHTML = `<span class="ios-icon">${_NAV_ICONS[item.icon]}</span><span class="ios-label">${item.label}</span>`;
      if (item.danger) {
        btn.onclick = () => emergencyStop();
      } else {
        btn.onclick = () => nav(item.page, btn);
      }
      navEl.appendChild(btn);
    });
    // Instantly place pill on first non-danger item
    const first = navEl.querySelector('.ios-item:not(.ios-danger)');
    if (first) {
      first.classList.add('active');
      requestAnimationFrame(() => movePill(first, true));
    }
  }

  // ── Navigation ────────────────────────────────────────────
  function nav(pageId, navEl) {
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
    const pg = $('page-' + pageId);
    if (pg) {
      requestAnimationFrame(() => {
        pg.classList.add('active');
        pg.style.opacity = '0';
        requestAnimationFrame(() => { pg.style.opacity = '1'; });
      });
    }
    if (navEl) { navEl.classList.add('active'); movePill(navEl); }
    if (pageId === 'a-email')    loadEmailCfg();
    if (pageId === 'a-settings') loadSysCfg();
    if (pageId === 'a-logs') {
      if (_logsUnlocked) {
        fetchAndRenderLogs();
      } else {
        $('logs-gate')?.classList.remove('hidden');
        setTimeout(() => $('lg-key')?.focus(), 80);
      }
    }
    if (pageId === 'a-cmds')     loadCmds();
  }

  // ── Mobile sidebar (no-ops — replaced by iOS nav) ─────────
  function toggleMenu() {}
  function closeMobileMenu() {}

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
    _ws.onopen = () => _syncPendingEvents();
    _ws.onmessage = e => { try { tick(JSON.parse(e.data)); } catch(_) {} };
    _ws.onclose   = () => setTimeout(connectWS, 3000);
  }

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

  function tick(s) {
    // Alert banner
    const banner = $('alert-banner');
    if (s.alert_active) {
      banner.classList.add('visible');
      $('pipeline-dot').className = 'hdr-dot alert';
      $('pipeline-label').textContent = 'Alert';
      $('hud-dot').className = 'hdr-dot alert';
      $('hud-label').textContent = 'Alert';
      // Alert activity is recorded server-side; heatmap updates via WS state
    } else {
      banner.classList.remove('visible');
      $('pipeline-dot').className = 'hdr-dot online';
      $('pipeline-label').textContent = 'Online';
      $('hud-dot').className = 'hdr-dot online';
      $('hud-label').textContent = 'Online';
      if (_prevAlertActive) _lastDetInfo = ''; // reset so next alert adds fresh entries
    }

    // Push notification on new alert
    if (s.alert_active && !_lastAlertState) {
      if (Notification.permission === 'granted') {
        new Notification('Garuda Alert', { body: s.danger_info || 'Danger detected \u2014 check camera feed', icon: '/static/favicon.ico' });
      }
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    }
    _lastAlertState = s.alert_active;

    _prevAlertActive = !!s.alert_active;

    // Modes
    renderModes(s.modes);

    // Security status card
    const card = $('status-card');
    if (card) {
      if (s.alert_active) {
        card.classList.add('alert');
        setText('status-label', 'ALERT');
        setText('status-desc', s.danger_info || 'Threat detected');
      } else {
        card.classList.remove('alert');
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
      const logText = (s.system_log || []).join('\n');
      const con = $('sys-console');
      if (con) {
        const atBot = con.scrollTop + con.clientHeight >= con.scrollHeight - 8;
        con.textContent = logText;
        if (atBot) con.scrollTop = con.scrollHeight;
      }
    }

    // Activity feed
    _updateActivityFeed(s);

    // Log badge counts
    const lcSys = document.getElementById('log-count-system');
    if (lcSys) lcSys.textContent = (s.system_log || []).length || 0;
    const lcDet = document.getElementById('log-count-detection');
    if (lcDet) lcDet.textContent = s.detection_log_count || 0;
    const lcPres = document.getElementById('log-count-presence');
    if (lcPres) lcPres.textContent = s.presence_log_count || 0;
    const lcVoice = document.getElementById('log-count-voice');
    if (lcVoice) lcVoice.textContent = ((s.voice_log || []).length + (s.voice_responses || []).length) || 0;

    // Narada feed (conversation-style)
    _updateNaradaFeed(s.voice_log || [], s.voice_responses || []);

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
    if (icon) {
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
    if (descEl) descEl.textContent = desc;
  }

  // ── Narada conversation feed ──────────────────────────────
  let _lastNaradaKey = '';
  function _updateNaradaFeed(voiceLog, voiceResponses) {
    const feed = document.getElementById('narada-feed');
    if (!feed) return;
    const key = voiceLog.length + ':' + voiceResponses.length;
    if (key === _lastNaradaKey) return;
    _lastNaradaKey = key;

    // Interleave voice inputs and responses
    const items = [];
    const maxLen = Math.max(voiceLog.length, voiceResponses.length);
    for (let i = 0; i < maxLen; i++) {
      if (i < voiceLog.length) items.push({ type: 'user', text: voiceLog[i] });
      if (i < voiceResponses.length) items.push({ type: 'assistant', text: voiceResponses[i] });
    }

    if (!items.length) {
      feed.innerHTML = '<div class="empty-state"><div class="empty-state-icon"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M12 2a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z"/><path d="M5 11a7 7 0 0 0 14 0"/></svg></div><span>Speak a command to begin</span></div>';
      return;
    }

    const atBot = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 8;
    feed.innerHTML = items.map(item =>
      `<div class="narada-msg ${item.type}">${esc(item.text)}</div>`
    ).join('');
    if (atBot) feed.scrollTop = feed.scrollHeight;
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

  // ── Admin: System settings ────────────────────────────────
  async function loadSysCfg() {
    try {
      const cfg = await api('GET', '/api/config'); _cfg = cfg;
      const t = Math.round((cfg.detection_threshold || 0.3) * 100);
      $('thr-slider').value = t;
      $('thr-val').textContent = (t / 100).toFixed(2);
      _privacyOn = cfg.privacy !== undefined ? cfg.privacy : true;
      $('priv-toggle').className = 'toggle' + (_privacyOn ? ' on' : '');
      const wl = $('watch-labels');
      if (wl) wl.value = (cfg.watch_labels || []).join(', ');
      const dl = $('danger-lbl');
      if (dl && cfg.danger_label) dl.value = cfg.danger_label;
      const gk = $('groq-api-key');
      if (gk) gk.value = cfg.groq_api_key || '';
    } catch(e) {}
    loadDevices();
    loadMasterKeys();
  }

  function togglePrivacy() {
    _privacyOn = !_privacyOn;
    $('priv-toggle').className = 'toggle' + (_privacyOn ? ' on' : '');
  }

  async function saveSettings() {
    const thr = parseInt($('thr-slider').value) / 100;
    const dl = val('danger-lbl') || undefined;
    const wlRaw = val('watch-labels') || '';
    const watchLabels = wlRaw.split(',').map(s => s.trim()).filter(Boolean);
    try {
      const groqKey = val('groq-api-key');
      await api('POST', '/api/config', {
        detection_threshold: thr,
        privacy: _privacyOn,
        watch_labels: watchLabels,
        ...(dl ? { danger_label: dl } : {}),
        ...(groqKey !== undefined ? { groq_api_key: groqKey } : {})
      });
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
    const tok  = _token || (base ? localStorage.getItem('garuda_token') : null);
    const url  = (base ? base.replace(/\/$/, '') : '') + '/api/logs/download';
    const headers = {};
    if (tok) headers['X-Garuda-Token'] = tok;
    try {
      const r = await fetch(url, { method: 'GET', headers, credentials: base ? 'omit' : 'include' });
      if (!r.ok) { alert('Download failed — make sure logs are unlocked.'); return; }
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `garuda-full-log-${new Date().toISOString().slice(0,10)}.txt`;
      a.click();
    } catch(e) {
      alert('Download error: ' + (e.message || e));
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
    try {
      await api('POST', '/api/devices/delete', { mac });
      loadDevices();
    } catch(e) {}
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

  async function api(method, url, body) {
    const base = getBackend();
    const fullUrl = base ? base.replace(/\/$/, '') + url : url;
    const headers = { 'Content-Type': 'application/json' };
    const tok = _token || (base ? localStorage.getItem('garuda_token') : null);
    if (tok) headers['X-Garuda-Token'] = tok;
    const opts = { method, headers, credentials: base ? 'omit' : 'include' };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(fullUrl, opts);
    let d;
    try { d = await r.json(); } catch(_) { d = { detail: r.statusText || `HTTP ${r.status}` }; }
    if (!r.ok) throw d;
    return d;
  }

  // ── Public API ────────────────────────────────────────────
  return {
    init,
    submitLogin, logout,
    goAdminFlow, backToMain, backToAdminStep1, sendAdminOTP, verifyAdminOTP,
    goMasterKey, submitMasterKeyLogin, unlockLogs,
    goForgot, sendForgotOTP, doReset,
    nav, toggleMode, emergencyStop,
    openBackendConfig, saveBackendConfig,
    toggleMenu, closeMobileMenu,
    toggleCamera, openDocs, sendChat, clearChat, toggleRateLimitInfo,
    loadEmailCfg, saveEmail, testEmail,
    loadSysCfg, togglePrivacy, saveSettings,
    filterLogs, exportLogs, downloadFullLog,
    loadDevices, addDevice, deleteDevice, scanNetwork, _regFromScan, refreshPresence,
    loadMasterKeys, requestMkOtp, addMasterKey, deleteMasterKey, onMkKeyInput,
    loadCmds, openAddCmd, addCmd, _delCmd,
    closeModal,
    switchLogTab,
    switchDocsTab,
    showToast,
  };
})();

document.addEventListener('DOMContentLoaded', G.init);

// Close modal on overlay click
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) e.target.classList.add('hidden');
});

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

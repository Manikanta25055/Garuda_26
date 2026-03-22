/* ============================================================
   Garuda — SPA logic
   ============================================================ */
const G = (() => {

  // ── State ────────────────────────────────────────────────
  let _session = null;   // { role, username, display_name }
  let _ws = null;
  let _pendingAdmin = null;  // { username } during OTP
  let _privacyOn = true;
  let _allLogs = [];
  let _cfg = {};

  const SWATCH_COLORS = [
    '#2997ff','#34c759','#ff3b30','#ff9f0a',
    '#af52de','#5e5ce6','#00c7be','#ff375f','#636366'
  ];

  const MODE_CFG = [
    { key:'dnd',       label:'DND',       cls:'' },
    { key:'night',     label:'Night',     cls:'yellow' },
    { key:'emergency', label:'Emergency', cls:'' },
    { key:'idle',      label:'Idle',      cls:'blue' },
    { key:'email_off', label:'Email Off', cls:'' },
    { key:'privacy',   label:'Privacy',   cls:'blue' },
  ];

  // ── Boot ─────────────────────────────────────────────────
  async function init() {
    buildSwatches('m-swatches');
    await loadProfiles();
  }

  // ── Login screen ─────────────────────────────────────────
  async function loadProfiles() {
    const el = $('profile-cards');
    el.innerHTML = '';
    try {
      const users = await api('GET', '/api/users-public');
      users.forEach(u => {
        const card = mk('div', 'profile-card');
        card.innerHTML = `
          <div class="profile-avatar" style="background:${u.box_color}">${u.display_name[0].toUpperCase()}</div>
          <div class="profile-name">${esc(u.display_name)}</div>`;
        card.onclick = () => openLoginPanel(u.username, u.display_name, u.box_color, false);
        el.appendChild(card);
      });
    } catch(e) {
      el.innerHTML = '<p style="color:var(--t3)">Could not load users.</p>';
    }
    // Admin card
    const adm = mk('div','profile-card');
    adm.innerHTML = `<div class="profile-avatar admin-av">🔐</div><div class="profile-name">Admin</div>`;
    adm.onclick = () => openLoginPanel('','Admin','',true);
    el.appendChild(adm);
  }

  function showProfiles() {
    hide('login-panel'); hide('admin-otp-screen'); hide('forgot-screen');
    show('profile-cards');
  }

  function openLoginPanel(uname, dname, color, isAdmin) {
    hide('profile-cards'); hide('admin-otp-screen'); hide('forgot-screen');
    show('login-panel');

    const av = $('lp-avatar');
    if (color) {
      av.style.background = color;
      av.style.borderRadius = '50%';
      av.style.width = '64px'; av.style.height = '64px';
      av.style.display = 'flex'; av.style.alignItems = 'center';
      av.style.justifyContent = 'center';
      av.style.fontSize = '24px'; av.style.fontWeight = '700'; av.style.color = '#fff';
      av.textContent = dname[0]?.toUpperCase() || '?';
    } else {
      av.style.cssText = '';
      av.textContent = '';
    }
    $('lp-title').textContent = dname || 'Admin';
    $('lp-sub').textContent   = uname ? `@${uname}` : 'Administrator account';
    $('li-user').value = uname;
    $('li-pass').value = '';
    $('li-err').classList.add('hidden');
    $('forgot-btn').classList.toggle('hidden', isAdmin);

    $('login-panel').dataset.admin = isAdmin ? '1' : '0';
    $('li-pass').focus();
  }

  async function submitLogin() {
    const un = $('li-user').value.trim();
    const pw = $('li-pass').value;
    const isAdmin = $('login-panel').dataset.admin === '1';
    const errEl = $('li-err');
    errEl.classList.add('hidden');

    if (!un || !pw) { showMsg(errEl,'Enter username and password.',false); return; }

    if (isAdmin) {
      try {
        const res = await api('POST','/api/admin/send-otp',{username:un,password:pw});
        _pendingAdmin = { username: un };
        hide('login-panel'); show('admin-otp-screen');
        $('otp-in').value = ''; $('otp-err').classList.add('hidden');
        $('otp-in').focus();
        if (!res.ok && res.bypass_otp)
          alert(`Email unavailable — dev OTP: ${res.bypass_otp}`);
      } catch(e) { showMsg(errEl, e.detail||'Invalid credentials.',false); }
    } else {
      try {
        _session = await api('POST','/api/login',{username:un,password:pw});
        afterLogin();
      } catch(e) { showMsg(errEl, e.detail||'Incorrect username or password.',false); }
    }
  }

  async function verifyOTP() {
    const otp = $('otp-in').value.trim();
    const errEl = $('otp-err');
    if (!otp || !_pendingAdmin) { showMsg(errEl,'Enter the OTP.',false); return; }
    try {
      _session = await api('POST','/api/admin/verify-otp',
                            {username:_pendingAdmin.username, otp});
      _pendingAdmin = null;
      afterLogin();
    } catch(e) { showMsg(errEl, e.detail||'Invalid OTP.',false); }
  }

  function afterLogin() {
    $('app').classList.add('logged-in');
    $('hdr-user').textContent = _session.display_name || _session.username;
    if (_session.role === 'admin') show('admin-nav');
    nav('dashboard', document.querySelector('[data-page="dashboard"]'));
    connectWS();
    if (_session.role === 'admin') loadCfg();
  }

  async function logout() {
    try { await api('POST','/api/logout',{}); } catch(_){}
    _session = null;
    if (_ws) { _ws.close(); _ws = null; }
    $('app').classList.remove('logged-in');
    hide('admin-nav');
    showProfiles();
    loadProfiles();
  }

  // ── Forgot password ───────────────────────────────────────
  function goForgot() {
    const un = $('li-user').value.trim();
    hide('login-panel'); show('forgot-screen');
    $('fp-user').value = un;
    $('fp-otp-block').classList.add('hidden');
    $('fp-msg').classList.add('hidden');
  }

  async function sendForgotOTP() {
    const un = $('fp-user').value.trim();
    if (!un) { showEl('fp-msg','Enter your username.',false); return; }
    try {
      const r = await api('POST','/api/forgot/send-otp',{username:un});
      if (r.bypass_otp) showEl('fp-msg',`Dev OTP: ${r.bypass_otp}`,false);
      else showEl('fp-msg','OTP sent to alert email.',true);
      $('fp-otp-block').classList.remove('hidden');
    } catch(e) { showEl('fp-msg', e.detail||'Failed.',false); }
  }

  async function doReset() {
    const otp = $('fp-otp').value.trim();
    const pw  = $('fp-newpass').value;
    if (!otp||!pw) { showEl('fp-msg','Enter OTP and new password.',false); return; }
    try {
      await api('POST','/api/forgot/reset',{otp,new_password:pw});
      showEl('fp-msg','Password reset! Redirecting…',true);
      setTimeout(showProfiles, 1800);
    } catch(e) { showEl('fp-msg', e.detail||'Invalid OTP.',false); }
  }

  // ── Navigation ────────────────────────────────────────────
  function nav(pageId, navEl) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const pg = $('page-'+pageId); if (pg) pg.classList.add('active');
    if (navEl) navEl.classList.add('active');
    // Lazy-load admin data
    if (pageId==='a-users')    loadUsers();
    if (pageId==='a-email')    loadEmailCfg();
    if (pageId==='a-settings') loadSysCfg();
    if (pageId==='a-logs')     renderLogs();
    if (pageId==='a-cmds')     loadCmds();
  }

  // ── WebSocket ─────────────────────────────────────────────
  function connectWS() {
    if (_ws) _ws.close();
    const proto = location.protocol==='https:'?'wss':'ws';
    _ws = new WebSocket(`${proto}://${location.host}/ws`);
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
    } else {
      banner.classList.remove('visible');
      $('pipeline-dot').className = 'hdr-dot online';
      $('pipeline-label').textContent = 'Online';
    }

    // Modes
    renderModes(s.modes);

    // Stats
    setText('s-uptime', s.uptime||'—');
    setText('s-det',    s.detections_today||'0');
    setText('s-alert',  s.last_alert ? s.last_alert.substring(11,19) : '—');
    setText('s-thr',    s.detection_threshold ? s.detection_threshold.toFixed(2) : '—');

    // Detection feed
    setText('det-feed', s.detection_info||'No detections.');

    // Console
    const con = $('sys-console');
    if (con) {
      const atBot = con.scrollTop+con.clientHeight >= con.scrollHeight-8;
      con.textContent = (s.system_log||[]).join('\n');
      if (atBot) con.scrollTop = con.scrollHeight;
    }

    // Narada
    renderLog('narada-vlog',  s.voice_log||[],       false);
    renderLog('narada-resp',  s.voice_responses||[], true);

    // Admin logs (live update)
    _allLogs = s.system_log||[];
    renderLogs();
    const av = $('a-vlog');
    if (av) {
      av.innerHTML = [...(s.voice_log||[]),...(s.voice_responses||[])].map(l=>`<div class="log-line">${esc(l)}</div>`).join('');
      av.scrollTop = av.scrollHeight;
    }
  }

  function renderLog(id, lines, isResp) {
    const el = $(id); if (!el) return;
    const atBot = el.scrollTop+el.clientHeight >= el.scrollHeight-8;
    el.innerHTML = lines.map(l=>`<div class="log-line${isResp?' response':''}">${esc(l)}</div>`).join('');
    if (atBot) el.scrollTop = el.scrollHeight;
  }

  function renderModes(modes) {
    const grid   = $('modes-pills');
    const hpills = $('header-pills');
    if (!grid) return;

    // Dashboard pills
    grid.innerHTML = '';
    MODE_CFG.forEach(m => {
      const pill = mk('span','mode-pill'+(modes[m.key]?` active ${m.cls}`:''));
      pill.innerHTML = `<span class="pill-dot"></span>${m.label}`;
      pill.onclick = () => toggleMode(m.key, modes[m.key]);
      grid.appendChild(pill);
    });

    // Header — only active modes
    if (hpills) {
      hpills.innerHTML = '';
      MODE_CFG.filter(m=>modes[m.key]).forEach(m=>{
        const p = mk('span',`mode-pill active ${m.cls}`);
        p.style.cssText = 'font-size:11px;padding:3px 10px';
        p.textContent = m.label;
        hpills.appendChild(p);
      });
    }
  }

  async function toggleMode(mode, currentVal) {
    try { await api('POST','/api/modes',{mode, value:!currentVal}); }
    catch(e){ console.error(e); }
  }

  // ── Admin: load config ────────────────────────────────────
  async function loadCfg() {
    try { _cfg = await api('GET','/api/config'); } catch(_){}
  }

  // ── Admin: Users ──────────────────────────────────────────
  async function loadUsers() {
    try {
      const data = await api('GET','/api/users');
      const tb = $('u-tbody'); tb.innerHTML='';
      Object.entries(data).forEach(([un,u])=>{
        const tr = mk('tr');
        tr.innerHTML=`
          <td style="font-family:var(--mono);font-size:12px">${esc(un)}</td>
          <td>${esc(u.display_name)}</td>
          <td><span class="mode-pill${u.role==='admin'?' active blue':''}" style="cursor:default;font-size:11px">${u.role}</span></td>
          <td><span class="color-dot" style="background:${u.box_color}"></span></td>
          <td class="flex gap-8">
            <button class="btn btn-ghost btn-sm" onclick='G._editUser(${JSON.stringify(un)},${JSON.stringify(u.display_name)},${JSON.stringify(u.box_color)})'>Edit</button>
            ${un!=='admin'?`<button class="btn btn-danger btn-sm" onclick='G._delUser(${JSON.stringify(un)})'>Delete</button>`:''}
          </td>`;
        tb.appendChild(tr);
      });
    } catch(e){ console.error(e); }
  }

  function openAddUser() {
    $('m-uname').value=''; $('m-upass').value=''; $('m-dname').value='';
    $('m-color').value='#2997ff'; $('m-role').value='user';
    $('m-add-err').classList.add('hidden');
    show('m-add-user');
  }

  async function addUser() {
    const un=val('m-uname'), pw=val('m-upass'), dn=val('m-dname');
    const color=$('m-color').value, role=$('m-role').value;
    const err=$('m-add-err');
    if(!un||!pw){showMsg(err,'Username and password required.',false);return;}
    try {
      await api('POST','/api/users/add',{username:un,password:pw,
                display_name:dn||un,box_color:color,role});
      closeModal('m-add-user'); loadUsers();
    } catch(e){showMsg(err,e.detail||'Failed.',false);}
  }

  function _editUser(un,dn,col){
    $('m-edit-un').value=un;
    $('m-edit-title').textContent=`Edit — ${un}`;
    $('m-edit-dn').value=dn; $('m-edit-pw').value=''; $('m-edit-col').value=col;
    $('m-edit-err').classList.add('hidden');
    show('m-edit-user');
  }

  async function saveUser(){
    const un=$('m-edit-un').value, dn=val('m-edit-dn');
    const pw=val('m-edit-pw'), col=$('m-edit-col').value;
    const err=$('m-edit-err'), payload={username:un,display_name:dn,box_color:col};
    if(pw) payload.new_password=pw;
    try{
      await api('POST','/api/users/update',payload);
      closeModal('m-edit-user'); loadUsers();
    } catch(e){showMsg(err,e.detail||'Failed.',false);}
  }

  async function _delUser(un){
    if(!confirm(`Delete user "${un}"? This cannot be undone.`)) return;
    try{ await api('POST','/api/users/delete',{username:un}); loadUsers(); }
    catch(e){ alert(e.detail||'Failed.'); }
  }

  // ── Admin: Email ──────────────────────────────────────────
  async function loadEmailCfg(){
    try{
      const cfg=await api('GET','/api/config'); _cfg=cfg;
      $('e-sender').value=cfg.email_sender||'';
      $('e-pass').value='';
      $('e-recip').value=(cfg.email_recipients||[]).join(', ');
      $('e-cool').value=cfg.email_cooldown||60;
    }catch(e){}
  }

  async function saveEmail(){
    const payload={
      email_sender:val('e-sender'),
      email_recipients:val('e-recip').split(',').map(s=>s.trim()).filter(Boolean),
      email_cooldown:parseInt($('e-cool').value)||60,
    };
    const pw=val('e-pass'); if(pw) payload.email_sender_pass=pw;
    try{ await api('POST','/api/config',payload); showEl('e-msg','Saved.',true); }
    catch(e){ showEl('e-msg',e.detail||'Failed.',false); }
  }

  async function testEmail(){
    showEl('e-msg','Sending…',true);
    try{
      const r=await api('POST','/api/email/test',{});
      showEl('e-msg', r.ok?'Test email sent!':'Failed: '+r.error, r.ok);
    }catch(e){ showEl('e-msg',e.detail||'Failed.',false); }
  }

  // ── Admin: System settings ────────────────────────────────
  async function loadSysCfg(){
    try{
      const cfg=await api('GET','/api/config'); _cfg=cfg;
      const t=Math.round((cfg.detection_threshold||0.3)*100);
      $('thr-slider').value=t;
      $('thr-val').textContent=(t/100).toFixed(2);
      _privacyOn=cfg.privacy!==undefined?cfg.privacy:true;
      $('priv-toggle').className='toggle'+(_privacyOn?' on':'');
    }catch(e){}
  }

  function togglePrivacy(){
    _privacyOn=!_privacyOn;
    $('priv-toggle').className='toggle'+(_privacyOn?' on':'');
  }

  async function saveSettings(){
    const thr=parseInt($('thr-slider').value)/100;
    const dl=val('danger-lbl')||undefined;
    try{
      await api('POST','/api/config',{detection_threshold:thr,privacy:_privacyOn,...(dl?{danger_label:dl}:{})});
      showEl('sys-msg','Settings saved.',true);
    }catch(e){ showEl('sys-msg',e.detail||'Failed.',false); }
  }

  // ── Admin: Logs ───────────────────────────────────────────
  function renderLogs(){
    const q=(val('log-q')||'').toLowerCase();
    const el=$('a-syslog'); if(!el) return;
    const lines=_allLogs.filter(l=>!q||l.toLowerCase().includes(q));
    el.textContent=lines.join('\n');
    el.scrollTop=el.scrollHeight;
  }

  function filterLogs(){ renderLogs(); }

  function exportLogs(){
    const blob=new Blob([_allLogs.join('\n')],{type:'text/plain'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=`garuda-logs-${new Date().toISOString().slice(0,10)}.txt`;
    a.click();
  }

  // ── Admin: Commands ───────────────────────────────────────
  async function loadCmds(){
    try{
      const cfg=await api('GET','/api/config');
      const cmds=cfg.custom_voice_commands||{};
      const tb=$('cmd-tbody'); tb.innerHTML='';
      if(!Object.keys(cmds).length){
        tb.innerHTML='<tr><td colspan="3" style="color:var(--t3);text-align:center;padding:20px">No custom commands yet.</td></tr>';
        return;
      }
      Object.entries(cmds).forEach(([phrase,resp])=>{
        const tr=mk('tr');
        tr.innerHTML=`
          <td style="font-family:var(--mono);font-size:12px;color:var(--accent)">${esc(phrase)}</td>
          <td style="color:var(--t2)">${esc(resp)}</td>
          <td><button class="btn btn-ghost btn-sm" onclick='G._delCmd(${JSON.stringify(phrase)})'>Delete</button></td>`;
        tb.appendChild(tr);
      });
    }catch(e){}
  }

  function openAddCmd(){
    $('m-phrase').value=''; $('m-resp').value='';
    show('m-add-cmd');
  }

  async function addCmd(){
    const phrase=val('m-phrase').toLowerCase();
    const resp=val('m-resp');
    if(!phrase||!resp){alert('Enter both fields.');return;}
    try{ await api('POST','/api/config/command/add',{phrase,response:resp}); closeModal('m-add-cmd'); loadCmds(); }
    catch(e){ alert(e.detail||'Failed.'); }
  }

  async function _delCmd(phrase){
    if(!confirm(`Delete "${phrase}"?`)) return;
    try{ await api('POST','/api/config/command/delete',{phrase}); loadCmds(); }
    catch(e){ alert(e.detail||'Failed.'); }
  }

  // ── Emergency Stop ────────────────────────────────────────
  async function emergencyStop(){
    if(!confirm('Stop the entire Garuda system now?')) return;
    await api('POST','/api/emergency-stop',{});
  }

  // ── Color swatches ────────────────────────────────────────
  function buildSwatches(containerId){
    const c=$(containerId); if(!c) return;
    SWATCH_COLORS.forEach(color=>{
      const s=mk('div','swatch');
      s.style.background=color;
      s.onclick=()=>{ $('m-color').value=color; };
      c.appendChild(s);
    });
  }

  // ── Utils ─────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const val = id => ($(id)?.value||'').trim();
  const setText = (id,v) => { const e=$(id); if(e) e.textContent=v; };
  const show = id => $(id)?.classList.remove('hidden');
  const hide = id => $(id)?.classList.add('hidden');
  const mk = (tag, cls='') => { const e=document.createElement(tag); if(cls) e.className=cls; return e; };
  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const closeModal = id => hide(id);

  function showMsg(el, txt, ok){
    el.textContent=txt;
    el.className='msg '+(ok?'ok':'err');
    el.classList.remove('hidden');
  }
  function showEl(id, txt, ok){
    showMsg($(id), txt, ok);
  }

  async function api(method, url, body){
    const opts = { method, headers:{'Content-Type':'application/json'} };
    if(body!==undefined) opts.body=JSON.stringify(body);
    const r = await fetch(url, opts);
    const d = await r.json();
    if(!r.ok) throw d;
    return d;
  }

  // ── Public API ────────────────────────────────────────────
  return {
    init, showProfiles, submitLogin, verifyOTP, logout,
    goForgot, sendForgotOTP, doReset,
    nav, toggleMode, emergencyStop,
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
  if(e.target.classList.contains('modal-overlay')) e.target.classList.add('hidden');
});

// Enter key to submit
document.addEventListener('keydown', e => {
  if(e.key !== 'Enter') return;
  if(!document.getElementById('login-panel')?.classList.contains('hidden')) G.submitLogin();
  else if(!document.getElementById('admin-otp-screen')?.classList.contains('hidden')) G.verifyOTP();
});

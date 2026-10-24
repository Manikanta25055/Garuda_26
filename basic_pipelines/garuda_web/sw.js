// Garuda Service Worker — enables PWA installability + offline shell caching
const CACHE = 'garuda-v52';
const SHELL = ['/static/style.css', '/static/sound.js', '/static/actions.js', '/static/app.js', '/static/home.js', '/static/narada.js', '/static/island.js', '/static/zones.js'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Only same-origin GETs for the page and its static files are handled here.
// Everything else (API, streams, WebSockets, other origins) goes straight to
// the network: answering those from a cache hid a live backend behind stale
// data and made a new browser report "cannot connect".
const BYPASS = ['/api', '/ws', '/stream', '/webrtc', '/narada'];

function putInCache(req, res) {
  if (res && res.ok && res.type === 'basic') {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
  }
  return res;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (BYPASS.some(p => url.pathname === p || url.pathname.startsWith(p + '/'))) return;

  // The page: network first, so a deploy shows up on the next load; the
  // cached copy is only for when the Pi cannot be reached.
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then(res => putInCache(req, res))
        .catch(() => caches.match(req).then(c => c || caches.match('/')))
    );
    return;
  }

  // Static files: the network first as well, the cache only when the Pi
  // cannot be reached. Cache-first ran the old script and style for one load
  // after every deploy, against the new server: a fix looked as if it had not
  // happened (log lines still run together) until a second reload.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      fetch(req).then(res => putInCache(req, res))
        .catch(() => caches.match(req).then(c => c || Response.error()))
    );
  }
});

// Phone alerts (garuda_routes/push.py). The message arrives encrypted and the
// browser decrypts it; the picture is inside it, so nothing is fetched.
self.addEventListener('push', e => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch (_) { m = { body: e.data ? e.data.text() : '' }; }
  const opts = {
    body: m.body || '', icon: '/static/icon-192.png', badge: '/static/icon-192.png',
    tag: m.tag || 'garuda', renotify: true, timestamp: m.ts ? m.ts * 1000 : Date.now(),
    data: { url: m.url || '/' },
    actions: m.kind === 'test' ? [] : [{ action: 'view', title: 'View' }, { action: 'silence', title: 'Silence 30 min' }],
  };
  if (m.image) opts.image = m.image;
  e.waitUntil(self.registration.showNotification(m.title || 'Garuda', opts));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  if (e.action === 'silence') {
    e.waitUntil(fetch('/api/push/silence', { method: 'POST', credentials: 'include' }).catch(() => {}));
    return;
  }
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const open = list.find(c => new URL(c.url).origin === self.location.origin);
    if (open) { open.postMessage({ nav: url.split('#')[1] || 'dashboard' }); return open.focus(); }
    return self.clients.openWindow(url);
  }));
});

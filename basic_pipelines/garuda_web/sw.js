// Garuda Service Worker — enables PWA installability + offline shell caching
const CACHE = 'garuda-v20';
const SHELL = ['/static/style.css', '/static/app.js', '/static/home.js', '/static/narada.js', '/static/island.js'];

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

  // Static files: answer from the cache at once and refresh it behind the
  // scenes, so nothing is ever more than one load out of date.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req).then(cached => {
        const fresh = fetch(req).then(res => putInCache(req, res)).catch(() => cached);
        return cached || fresh;
      })
    );
  }
});

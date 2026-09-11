// Service Worker – Tippcup v5
const CACHE = 'tippcup-v7';

const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(cache =>
      Promise.allSettled(STATIC_ASSETS.map(url => cache.add(url)))
    )
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (['/api','/push','/chat','/'].some(p => url.pathname.startsWith(p) && url.pathname !== '/static/')) return;
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});

// ── Push Notifications ────────────────────────────────────────
self.addEventListener('push', e => {
  let d = { title: 'Tippcup', body: 'Neue Benachrichtigung', url: '/' };
  if (e.data) {
    try { d = { ...d, ...e.data.json() }; }
    catch { d.body = e.data.text() || d.body; }
  }

  // Minimale Optionen — maximale Browser-Kompatibilität
  const options = {
    body:  d.body,
    icon:  '/static/icon-192.png',
    data:  { url: d.url },
    tag:   'tippcup-' + Date.now(),
  };

  e.waitUntil(
    self.registration.showNotification(d.title, options)
      .catch(err => {
        // Fallback ohne icon falls icon-Fehler
        return self.registration.showNotification(d.title, { body: d.body, data: { url: d.url } });
      })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(cs => {
        const c = cs.find(c => c.url.includes(url));
        return c ? c.focus() : clients.openWindow(url);
      })
  );
});

// ── Firebase/Mozilla Subscription Rotation ───────────────────
self.addEventListener('pushsubscriptionchange', e => {
  const oldEp = e.oldSubscription ? e.oldSubscription.endpoint : null;
  e.waitUntil(
    self.registration.pushManager.subscribe(e.oldSubscription.options)
      .then(newSub => {
        const s = newSub.toJSON();
        return fetch('/push/renew', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            endpoint: s.endpoint,
            keys: { p256dh: s.keys.p256dh, auth: s.keys.auth },
            old_endpoint: oldEp
          })
        });
      })
  );
});

// Sofortiger Wechsel auf Anfrage
self.addEventListener('message', e => {
  if (e.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

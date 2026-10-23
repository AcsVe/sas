// App-shell cache + offline page only. No business data is cached:
// attendance records always need a live connection.
const CACHE_NAME = 'sas-shell-v1';
const SHELL = ['/static/offline.html', '/static/css/app.css', '/static/js/app.js',
  '/static/logo.png', '/static/icon-192.png', '/static/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ns) => Promise.all(ns.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))));
  self.clients.claim();
});
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/static/offline.html')));
    return;
  }
  const url = new URL(req.url);
  if (req.method === 'GET' && url.origin === location.origin && url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
  }
});

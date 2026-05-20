const CACHE_NAME = 'ants-replay-v1';
const STATIC_ASSETS = [
    '/visualizer/js/visualizer.js',
    '/visualizer/data/',
    '/visualizer/favicon.svg',
];

self.addEventListener('install', (e) => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS)).catch(() => {}));
    self.skipWaiting();
});

self.addEventListener('activate', (e) => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ));
    self.clients.claim();
});

self.addEventListener('fetch', (e) => {
    const url = new URL(e.request.url);
    // Cache replay data permanently (immutable once generated)
    if (url.pathname.startsWith('/replay_data/')) {
        e.respondWith(caches.open(CACHE_NAME).then(async (cache) => {
            const cached = await cache.match(e.request);
            if (cached) return cached;
            const res = await fetch(e.request);
            if (res.ok) cache.put(e.request, res.clone());
            return res;
        }));
        return;
    }
    // Cache visualizer static assets (stale-while-revalidate)
    if (url.pathname.startsWith('/visualizer/')) {
        e.respondWith(caches.open(CACHE_NAME).then(async (cache) => {
            const cached = await cache.match(e.request);
            const fetchPromise = fetch(e.request).then(res => {
                if (res.ok) cache.put(e.request, res.clone());
                return res;
            }).catch(() => cached);
            return cached || fetchPromise;
        }));
        return;
    }
});

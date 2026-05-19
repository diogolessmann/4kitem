/**
 * Service Worker — Lessmann Despachante
 * Estratégia: cache-first para assets estáticos, network-first para páginas HTML
 */

const CACHE_NAME = 'lessmann-v1';
const STATIC_ASSETS = [
  '/despachante/manifest.json',
  '/static/desp/icon-192.png',
  '/static/desp/icon-512.png',
];

// Instala e faz cache dos assets estáticos essenciais
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(() => {/* ignora falhas de rede */});
    })
  );
  self.skipWaiting();
});

// Limpa caches antigos na ativação
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Estratégia de fetch
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Ignora requisições não-GET
  if (event.request.method !== 'GET') return;

  // Ignora APIs dinâmicas (sempre network)
  if (url.pathname.startsWith('/despachante/api/') ||
      url.pathname.includes('/print/') ||
      url.pathname.includes('/retencao/csv')) return;

  // Assets estáticos: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache =>
        cache.match(event.request).then(cached => {
          if (cached) return cached;
          return fetch(event.request).then(res => {
            if (res && res.status === 200) cache.put(event.request, res.clone());
            return res;
          }).catch(() => cached);
        })
      )
    );
    return;
  }

  // Google Fonts: cache-first (evita latência de carregamento)
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache =>
        cache.match(event.request).then(cached => {
          if (cached) return cached;
          return fetch(event.request).then(res => {
            if (res && res.status === 200) cache.put(event.request, res.clone());
            return res;
          }).catch(() => cached || new Response('', { status: 503 }));
        })
      )
    );
    return;
  }

  // Páginas HTML do despachante: network-first com fallback de cache
  if (url.pathname.startsWith('/despachante')) {
    event.respondWith(
      fetch(event.request)
        .then(res => {
          if (res && res.status === 200) {
            const toCache = res.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, toCache));
          }
          return res;
        })
        .catch(() =>
          caches.match(event.request).then(cached =>
            cached || new Response(
              '<html><body style="font-family:sans-serif;text-align:center;padding:60px;color:#888">' +
              '<h2>📵 Sem conexão</h2><p>Reconecte para acessar o sistema.</p></body></html>',
              { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
            )
          )
        )
    );
  }
});

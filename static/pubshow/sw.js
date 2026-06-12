// PUBSHOW Jukebox — Service Worker
// Requisito para PWA instalável. Estratégia: network-first com fallback offline simples.
const CACHE_NAME = 'pubshow-jk-v2';
const OFFLINE_URLS = ['/static/pubshow/offline.html'];

self.addEventListener('install', event => {
  self.skipWaiting();
  // Pre-cache página offline (criada dinamicamente se não existir)
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      cache.addAll(OFFLINE_URLS).catch(() => {})
    )
  );
});

self.addEventListener('activate', event => {
  // Limpa caches antigas
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Só intercepta requests do mesmo origin
  if (url.origin !== self.location.origin) return;

  // Estáticos (CSS/JS/imagens): cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(res => {
          // Só cacheia resposta BOA (200) — evita 404/erro grudar no cache pra sempre
          if (res && res.ok) {
            const clone = res.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          }
          return res;
        });
      })
    );
    return;
  }

  // Páginas jukebox: network-first, fallback offline
  if (url.pathname.startsWith('/pubshow/jukebox/')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match('/static/pubshow/offline.html').then(r =>
          r || new Response('<h2 style="font-family:sans-serif;text-align:center;padding:40px;color:#4ade80">Sem conexão 📵<br><small style="color:#888">Reconecte e tente novamente</small></h2>',
            { headers: { 'Content-Type': 'text/html' } })
        )
      )
    );
    return;
  }
});

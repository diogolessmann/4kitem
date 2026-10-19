// PUBSHOW Jukebox — Service Worker DESLIGADO (16/10/26, B8/CEL-18).
// O celular do cliente não é mais PWA (o banner cobria o botão do PIX e o registro com scope '/'
// era recusado pelo navegador). Se algum aparelho chegou a instalar o SW antigo, este aqui
// apaga os caches dele e se desregistra sozinho na próxima visita. Nada é servido de cache.
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.filter(k => k.startsWith('pubshow-jk')).map(k => caches.delete(k)));
    } catch (e) {}
    try { await self.registration.unregister(); } catch (e) {}
  })());
});

// Service Worker — PUBSHOW Painel
// Responsável por receber Web Push notifications quando chega pedido no bar

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

// ── Recebe notificação push ───────────────────────────────────────────────────
self.addEventListener('push', e => {
    let data = {};
    try { data = e.data ? e.data.json() : {}; } catch (_) {}

    const titulo = data.titulo || '🎵 Novo pedido!';
    const corpo  = data.corpo  || 'Um cliente fez um pedido no Jukebox';
    const url    = data.url    || '/pubshow/painel';

    e.waitUntil(
        self.registration.showNotification(titulo, {
            body:      corpo,
            icon:      '/static/pubshow/icon-192.png',
            badge:     '/static/pubshow/icon-192.png',
            vibrate:   [200, 100, 200, 100, 400],
            tag:       'pubshow-pedido',
            renotify:  true,
            requireInteraction: false,
            data: { url }
        })
    );
});

// ── Clique na notificação abre o painel ──────────────────────────────────────
self.addEventListener('notificationclick', e => {
    e.notification.close();
    const targetUrl = e.notification.data?.url || '/pubshow/painel';
    e.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
            for (const c of list) {
                if (c.url.includes('/pubshow/painel')) return c.focus();
            }
            return self.clients.openWindow(targetUrl);
        })
    );
});

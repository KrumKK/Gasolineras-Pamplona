/* Service worker de Gasolineras Pamplona (KKK)
   - La app (HTML, iconos) se guarda en el móvil y abre al instante, también sin conexión.
   - Los precios (data/*.json) se piden siempre a la red primero; sin conexión se usan los últimos guardados.
   Al cambiar index.html o los iconos, sube el número de VERSION para que los móviles descarguen la nueva versión. */
const VERSION = "kkk-v1";
const SHELL = VERSION + "-app";
const DATA = "kkk-datos";
const FONTS = "kkk-fuentes";
const APP_FILES = [
  "./", "./index.html", "./manifest.webmanifest",
  "./kkk-favicon.png", "./kkk-apple-touch-icon.png", "./kkk-logo.png", "./kkk-banner.jpg",
  "./kkk-icon-192.png", "./kkk-icon-512.png", "./kkk-icon-maskable-512.png"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(APP_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k.endsWith("-app") && k !== SHELL).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Precios: red primero, copia guardada si no hay conexión
  if (url.origin === location.origin && url.pathname.includes("/data/")) {
    e.respondWith(
      fetch(req).then(res => {
        if (res.ok) { const copy = res.clone(); caches.open(DATA).then(c => c.put(url.pathname, copy)); }
        return res;
      }).catch(() => caches.match(url.pathname, {cacheName: DATA}).then(r => r || Response.error()))
    );
    return;
  }

  // Tipografías de Google: guardadas tras la primera visita
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    e.respondWith(
      caches.open(FONTS).then(c => c.match(req).then(hit => hit || fetch(req).then(res => { c.put(req, res.clone()); return res; })))
    );
    return;
  }

  // App: copia guardada al instante y actualización en segundo plano
  if (url.origin === location.origin) {
    e.respondWith(
      caches.open(SHELL).then(c => c.match(req, {ignoreSearch: true}).then(hit => {
        const net = fetch(req).then(res => { if (res.ok) c.put(req, res.clone()); return res; }).catch(() => hit);
        return hit || net;
      }))
    );
  }
});

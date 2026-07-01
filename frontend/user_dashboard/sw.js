// ─────────────────────────────────────────────────────────────────────────────
// ResQNet User Dashboard — Service Worker
//
// Strategy:
//   - App shell (HTML/CSS/JS) → cache-first, so the dashboard loads instantly
//     and works offline once visited at least once.
//   - API calls (/user/*, /device/*) → network-first, falling back to a
//     cached copy of the LAST successful response when offline. This is
//     what makes "offline contacts" work: the last-fetched emergency
//     contacts list stays readable even with no signal.
// ─────────────────────────────────────────────────────────────────────────────

const CACHE_NAME = "resqnet-user-dashboard-v1";
const APP_SHELL = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Only handle GET requests — POST/PATCH/DELETE always go to network,
  // never cached (registering, updating contacts, etc. must be live).
  if (event.request.method !== "GET") return;

  // API calls to the backend: network-first, cache-fallback.
  // This is what keeps emergency contacts visible when offline.
  if (url.pathname.startsWith("/user/") || url.pathname.startsWith("/device/")) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // App shell: cache-first, network-fallback (standard PWA shell pattern)
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});

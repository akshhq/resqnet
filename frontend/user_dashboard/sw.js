// ─────────────────────────────────────────────────────────────────────────────
// ResQNet User Dashboard — Service Worker
//
// This worker now exists only to clean up older cached versions and
// unregister itself. The dashboard loads directly from the network.
// ─────────────────────────────────────────────────────────────────────────────

const CACHE_NAME = "resqnet-user-dashboard-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
      .then(() => self.registration.unregister())
  );
  self.clients.claim();
});

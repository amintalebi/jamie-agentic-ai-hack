// Service worker: makes the dashboard installable and lets the shell open offline.
// API calls are never cached: monitoring data must be live.
const CACHE = "jamie-shell-v2";
const SHELL = ["/app/", "/app/manifest.webmanifest", "/app/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Cache only successful responses: a cached 404 or 500 would break the app for good.
const keep = (request) => (res) => {
  if (res.ok) {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(request, copy));
  }
  return res;
};

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return; // network only
  }
  if (url.pathname.startsWith("/app/assets/")) {
    // Hashed build files never change: cache first.
    event.respondWith(
      caches.match(event.request).then((hit) => hit || fetch(event.request).then(keep(event.request))),
    );
    return;
  }
  // Pages and the rest: network first, fall back to the cached shell.
  event.respondWith(
    fetch(event.request)
      .then(keep(event.request))
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("/app/"))),
  );
});

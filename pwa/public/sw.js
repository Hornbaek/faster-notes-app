/*
 * Faster Notes service worker.
 *
 * Purpose: make the PWA load offline so you can record *away from home* (the
 * whole premise of the app). The app shell + hashed JS/CSS are cached on first
 * online visit; afterwards the app opens with no network. Recordings go into
 * IndexedDB and the sync layer uploads them when the server is reachable again.
 *
 * Caching policy:
 *   - GET navigations  -> serve the cached app shell ("/"), SPA router handles routes
 *   - GET static assets-> stale-while-revalidate (hashed /assets/*, icons, manifest)
 *   - API/bridge calls -> never cached; always network (so we never serve a stale
 *                         job status or replay an upload)
 *   - non-GET (uploads)-> not intercepted; go straight to the network
 *
 * Bump CACHE when the precache list or strategy changes to evict old caches.
 */
const CACHE = "faster-notes-v1";
const SHELL = "/";
const PRECACHE = [
  "/",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];
// Dynamic endpoints served by FastAPI — must always hit the network.
const API_PREFIXES = ["/status", "/upload", "/job", "/result", "/api"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      // Don't fail the whole install if one asset 404s.
      .then((c) => Promise.allSettled(PRECACHE.map((u) => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

function isApi(pathname) {
  return API_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + "/")
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // uploads / POSTs go straight to network

  const url = new URL(req.url);
  // Only handle our own origin; anything cross-origin goes to the network.
  if (url.origin !== self.location.origin) return;

  // API/bridge GETs: never cache (live job status, reachability check, etc.).
  if (isApi(url.pathname)) return;

  // SPA navigations: serve the cached shell so the app opens with no network.
  if (req.mode === "navigate") {
    event.respondWith(
      caches.match(SHELL).then((cached) => cached || fetch(req))
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(req);
      const network = fetch(req)
        .then((res) => {
          if (res && res.ok) cache.put(req, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});

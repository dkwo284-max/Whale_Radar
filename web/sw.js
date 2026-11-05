// 마켓 레이더 서비스 워커: 화면은 캐시 우선, 데이터(JSON)는 네트워크 우선(오프라인이면 마지막 데이터)
const SHELL = "radar-shell-v2";
const DATA = "radar-data-v1";
const SHELL_FILES = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg", "./icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/data/")) {
    e.respondWith(fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(DATA).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request)));
    return;
  }
  // 화면: 네트워크 먼저 시도해 새 버전을 받고, 실패하면 캐시
  e.respondWith(fetch(e.request).then((res) => {
    const copy = res.clone();
    caches.open(SHELL).then((c) => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match("./index.html"))));
});

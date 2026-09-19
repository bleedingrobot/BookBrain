// Network-first service worker: always serves fresh content when online,
// falls back to the last-seen response (and the app shell for navigations)
// when offline. The library list and metadata index live in localStorage,
// so an offline shell still renders a browsable library.
const CACHE = 'bookbrain-v2'
const SHELL = new URL('./', self.registration.scope).pathname

self.addEventListener('install', () => self.skipWaiting())

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return

  // Navigations and the hashed app bundle must never come from the browser's
  // own HTTP cache — bypass it so a deploy is picked up on the next load.
  const revalidate = req.mode === 'navigate' || url.pathname.includes('/assets/')
  const fetchReq = revalidate ? new Request(req, { cache: 'reload' }) : req

  event.respondWith(
    fetch(fetchReq)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone()
          caches.open(CACHE).then((c) => c.put(req, copy))
        }
        return res
      })
      .catch(() =>
        caches
          .match(req)
          .then((hit) => hit || (req.mode === 'navigate' ? caches.match(SHELL) : undefined)),
      ),
  )
})

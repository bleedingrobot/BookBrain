# Task 32 — SFF news feed (RSS → a "From around the SFF world" section)

Run as its own fresh session. **Read first:** `prompts/25-hardcover-integration.md`
(posture), `prompts/27-new-and-upcoming-releases.md` (the sidecar + strip
pattern this copies), `SPEC.md` § "Providers" / "Sidecars", and the memories
`project-bookbrain-metadata-sidecar`, `project-bookbrain-project`.

## The ask

James keeps a list of SFF sites with RSS feeds (reviews, cover reveals, awards
news, new-release roundups). He wants a section **under the release marquees**
in the library-viewer showing the **latest articles** — source, headline, a
sentence of description, a link out — so the viewer doubles as a genre-news
homepage.

**Not covers gliding past** (it's text, not books) — a compact vertical list /
card stack, styled to sit with the marquees. Newest first, all feeds merged.

### The feeds

**v2 set — 2026-09-10** (James: "some of these rss feeds ... are not really
about books"). Dropped **Reactor** + **File 770** (film / TV / comics / fandom
news) and **Swords & Spaceships** (a Book Riot newsletter archive, redundant).
Added review blogs off `feedspot.com/fantasy_book_rss_feeds`, each checked live.

| Source | Feed |
|---|---|
| Locus Online | `https://locusmag.com/feed/` |
| Grimdark Magazine | `https://www.grimdarkmagazine.com/feed/` |
| Fantasy Book Critic | `https://fantasybookcritic.blogspot.com/feeds/posts/default` (Blogger **Atom**) |
| The Fantasy Hive | `https://fantasy-hive.co.uk/feed/` |
| FanFiAddict | `https://fanfiaddict.com/feed/` |
| Book Riot — SFF | `https://bookriot.com/category/genre/science-fiction-fantasy/feed/` |
| Before We Go Blog | `https://beforewegoblog.com/feed/` |
| Fantasy Book Cafe | `https://fantasybookcafe.com/feed` |
| The Fantasy Inn | `https://thefantasyinn.com/feed` |
| Fantasy-Faction | `https://fantasy-faction.com/feed` |
| Pat's Fantasy Hotlist | `https://feeds.feedburner.com/PatsFantasyHotlist` |

Checked but skipped: **Fantasy Literature** (403s bots). Mixed RSS 2.0 + Atom,
mixed date formats, some WordPress, one Blogger, one FeedBurner. Parse
defensively.

## Architecture — same as every other sidecar

The library-viewer is a **static site**; it **cannot** fetch these feeds
(CORS: none of them send `Access-Control-Allow-Origin`). So, exactly like
`bookbrain-new-releases.json`:

**backend fetches + parses → writes `bookbrain-news.json` to the Drive library
folder → viewer reads it with a modifiedTime-gated cache.**

No Hardcover, no Anthropic, no auth on the fetch — just HTTP GET on public
feeds. Free. The only cost is ~9 HTTP requests per nightly run.

### Licence / etiquette

RSS is published *for* syndication. Showing **headline + short excerpt +
clear source attribution + a link back to the original** is standard, expected
use. **Do not** store or display full article bodies (truncate hard), **do
not** proxy or rehost, always link out (`target="_blank" rel="noreferrer"`).
Attribute every item to its source visibly.

---

## Part 1 — backend: fetch, parse, sidecar — SHIPPED 2026-09-10

`feedparser>=6.0` added. `app/services/sff_news_service.py` — `_FEEDS` (the 9
below; **`www.fantasybookcritic.com` no longer resolves → use
`fantasybookcritic.blogspot.com`**), `fetch_news()` fans out with
`asyncio.gather`, `feedparser.parse` in a thread, per-feed best-effort,
newest 6/feed, HTML unescape→strip→unescape, WordPress "The post…" +
"Continue reading →" + "Read more" trailers stripped, dedupe by link, sort by
`published` desc, cap 50. `library_index_service.regenerate_news` +
`NEWS_FILENAME`/`NEWS_VERSION`. Nightly step (no token needed, never fails
the run) + `POST /api/library/news`. Live: **50 items from all 9 feeds**.
Tests: `test_sff_news_service.py` (merge/sort/dedupe/clean, dead feed
skipped, all-down → empty, `_plain`). Backend 676 + corpus green.

### Original notes

### Dependency
Add **`feedparser>=6.0`** to `pyproject.toml` `dependencies`. Pure-Python,
handles RSS 2.0 / RSS 1.0 / Atom / the date-format zoo / relative links in one
call. (Alternative: `defusedxml` — already a dep — but then you hand-handle
Atom vs RSS, `<content:encoded>` vs `<summary>`, and 5 date formats. Not worth
it; use feedparser.)

### `app/services/sff_news_service.py`

```python
# v2 list — see "The feeds" above. The live module is the source of truth.
_FEEDS: list[tuple[str, str]] = [ ... ]
_PER_FEED = 6          # newest N entries per source
_SUMMARY_CAP = 280     # chars, after HTML strip
_TOTAL_CAP = 50
```

`async def fetch_news(*, client=None) -> list[dict]`:
- For each `(name, url)`: `httpx` GET (10s timeout, a real `User-Agent` — some
  WP hosts 403 the default), `feedparser.parse(resp.content)` **in a thread**
  (`asyncio.to_thread` — feedparser is sync and can be slow on big feeds).
- Per entry, take the newest `_PER_FEED`:
  - `title` — required, stripped
  - `link` — required, absolute (`urllib.parse.urljoin(feed_link, entry.link)`)
  - `summary` — `entry.summary` or `entry.get("content", [{}])[0].get("value")`;
    **strip HTML** (reuse a shared `_plain_text` — factor the one in
    `library_index_service` into `app/services/_text.py` or just a local copy)
    then truncate to `_SUMMARY_CAP` on a word boundary + "…"
  - `published` — `entry.published_parsed` / `updated_parsed` → ISO 8601 UTC,
    or `None`
  - `source` — `name`
- **Best-effort per feed**: any failure (timeout, 403, malformed, `bozo`) →
  log + skip that feed, keep the rest. Never raise.
- Merge, drop entries with no link/title, **dedupe by link**, sort by
  `published` desc (undated → end), cap `_TOTAL_CAP`.

### `library_index_service` (or a new tiny module)

- `NEWS_FILENAME = "bookbrain-news.json"`, `NEWS_VERSION = 1`.
- `async def regenerate_news(creds, library_folder_id) -> int | None` — mirror
  `regenerate_new_releases`: `fetch_news()` → wrap `{version, generatedAt,
  items}` → `_write_json_file`. Best-effort, never raises, no-op without creds.
  (The *fetch* needs no creds; the *write* does.)

### Wiring
- `nightly.py` `run_nightly` — a step after the new-releases step:
  `regenerate_news(creds, library_folder_id)`, token-gated? No — no token
  needed; just guard on `library_folder_id`. Never fails the run.
- Route `POST /api/library/news` → `regenerate_news(...)` (mirror
  `POST /api/library/new-releases`).

### Tests
- `tests/test_sff_news_service.py` — respx-mock 2–3 feeds (one RSS sample, one
  Atom sample, one that 404s), assert: merge + sort + dedupe + HTML strip +
  summary truncation + the dead feed is skipped not fatal. Keep a small
  RSS + Atom fixture string inline.
- `tests/test_library_index_service.py` — `regenerate_news` writes the sidecar
  shape (stub `fetch_news`).
- Full `pytest -q` + `pytest -m corpus` green.

---

## Part 2 — viewer: the news section — SHIPPED 2026-09-10

`lib/news.ts` (`NewsItem`/`News`, `fetchNews` modifiedTime-cached, `normaliseNews`
drops non-http links, `timeAgo`). `components/NewsFeed.tsx` — a compact
"From around the SFF world" section (source chip + headline link-out + 2-line
`line-clamp` excerpt + relative time), latest 6, "More →". `components/NewsScreen.tsx`
— full 50 + a source-filter chip row, shared `NewsRow`. `App.tsx` — lazy
`fetchNews` alongside the others, `<NewsFeed>` under `<ReadNext>` gated on
`settings.showNews !== false` + `news.items.length`, `<NewsScreen>` route,
"SFF news" in the header menu. `settings.ts` `showNews` — **default ON**,
persists only the opt-out (`bookbrain.showNews === 'false'`); `SettingsForm`
checkbox. `news.test.ts`. Viewer 170 + build + lint green.

**Dismiss ("X away") — added 2026-09-10** (James asked), then **made
cross-device** (James asked again). `bookbrain-news-dismissed.json` Drive
sidecar (`{version:1, links:[]}`) is the source of truth;
`bookbrain.newsDismissed` localStorage is a cache for instant/offline render.
`news.ts`: `cachedDismissedNews()` (sync, for the `useState` init),
`dismissedNewsSynced(token, folder)` (reads the sidecar, **merges the cache
in** so an older local-only install seeds it rather than losing dismissals),
`dismissNewsItem(token, folder, link, current)` (read→merge-with-server→write,
**serialised** via a module promise-chain so rapid X-ing doesn't clobber —
no Drive locking), `pruneDismissedNews(token, folder, current, liveLinks)`
(writes only when something dropped). `NewsRow` `onDismiss` → a faint ✕
(opacity-40, full on hover — touch has no hover); `NewsFeed` / `NewsScreen`
filter dismissed out. `App`: `dismissedNews` inits from the cache, refreshes
via `dismissedNewsSynced` in the lazy effect alongside `fetchNews`, then
prunes. Viewer 186 green.

### Original notes

### `library-viewer/src/lib/news.ts`
Copy `newReleases.ts` almost verbatim:
- `NewsItem = { title, link, summary, published: string | null, source }`
- `News = { generatedAt: string; items: NewsItem[] }`, `EMPTY_NEWS`
- `fetchNews(token, libraryFolderId)` — modifiedTime-gated localStorage cache,
  `bookbrain.news` key, best-effort.
- `normaliseNews(raw)` — validate each item (string title + link), drop junk.

### `library-viewer/src/components/NewsFeed.tsx`
A compact section, **not** a `<Marquee>`:
- Heading "From around the SFF world" (or "Genre news").
- The latest ~6 items as rows: a small **source chip**, the **title as a link**
  (`target="_blank" rel="noreferrer"`, opens the original), a 2-line clamped
  **summary**, a muted **relative time** ("3h ago" / "2d ago" — reuse the
  `whenText` helper from `WishlistScreen`, or lift it to `lib/time.ts`).
- "More →" opens `<NewsScreen>`.

### `library-viewer/src/components/NewsScreen.tsx`
Full list (all 50), a **source filter** (chips: All + each source), same row
layout. Mirror `ActivityScreen` / `NewReleasesScreen` shell + back button.

### `App.tsx`
- Lazy `fetchNews` alongside `fetchNewReleases` (same `requestIdleCallback` /
  lazy-load effect).
- Render `<NewsFeed>` under the release marquees, **only when `news.items`
  non-empty**.
- A **setting** `showNews` (browser-local, like `showGlobalReleases`) —
  **default ON** (James asked for it); a `SettingsForm` checkbox to hide it.
- Optional: a "News" button in `LibraryHeader` next to Wishlist / Activity.

### Tests
`news.test.ts` — `normaliseNews` keeps valid items, drops junk, tolerates a
missing `items`. Viewer `npm test` + `npm run build` + `npm run lint` green.

---

## Part 3 (optional, later) — make the feed list editable

v1 bakes `_FEEDS` into the service. If James wants to add/remove feeds without
a deploy: a `bookbrain-feeds.json` in the Drive folder that the backend reads
(like `_read_wishlist_keys` does for the wishlist), falling back to `_FEEDS`.
Skip unless asked.

---

## Constraints / gotchas

- **Backend restart on Windows** — no `--reload`; kill by port
  (`netstat -ano | grep :8000` → `taskkill //F //PID`, not the `ps` pid).
  A stale `multiprocessing` child can keep serving old code — check
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` for a child of
  the dead uvicorn pid.
- **User-Agent** — set a real one on the feed GETs; some WordPress/Cloudflare
  hosts 403 `python-httpx/x.y`.
- **feedparser is sync** — always `asyncio.to_thread(feedparser.parse, ...)`.
- **Full-content feeds** — the summary strip + hard truncate is what keeps
  this fair-use and the sidecar small. Don't raise `_SUMMARY_CAP`.
- **Sidecar size** — 50 items × ~400 bytes ≈ 20KB, fine.
- **Dedupe by link**, not title (feeds occasionally cross-post).
- One commit for Part 1, one for Part 2. Update `prompts/README.md`,
  `ROADMAP.md`, and a new `project-bookbrain-sff-news` memory. Deploy is
  automatic on push (viewer); the sidecar needs `POST /api/library/news` +
  nothing else (no index regen — it's its own file).

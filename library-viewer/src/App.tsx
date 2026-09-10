import { useEffect, useMemo, useRef, useState } from 'react'
import { ActivityScreen } from './components/ActivityScreen'
import { BookList } from './components/BookList'
import { ContinueReading } from './components/ContinueReading'
import { DeviceLibrary } from './components/DeviceLibrary'
import { LibraryHeader } from './components/LibraryHeader'
import { Reader } from './components/Reader'
import { NewReleasesScreen } from './components/NewReleasesScreen'
import { NewsFeed } from './components/NewsFeed'
import { NewsScreen } from './components/NewsScreen'
import { PromptsScreen } from './components/PromptsScreen'
import { ReadingGoalBar, ReadingStatsScreen } from './components/ReadingStats'
import { RecentMarquee } from './components/RecentMarquee'
import { ReleaseCard } from './components/ReleaseCard'
import { ReleaseMarquee } from './components/ReleaseMarquee'
import { SettingsForm } from './components/SettingsForm'
import { SetupChecklist } from './components/SetupChecklist'
import { WhoAmI } from './components/WhoAmI'
import { WishlistScreen } from './components/WishlistScreen'
import { logActivity } from './lib/activityLog'
import { cacheStats, clearBookCache } from './lib/bookCache'
import {
  buildRows,
  matchesFilter,
  matchesRow,
  quickWinScore,
  sendKey,
  SORT_LABELS,
  SORTS,
  topGenres,
  topMoods,
  type FilterKey,
  type SendStatus,
  type SortKey,
} from './lib/books'
import { copyFileToFolder, downloadFile, type DriveFile } from './lib/drive'
import { pickRecentBooks } from './lib/marquee'
import {
  fetchRecommendations,
  type RecBook,
  type Recommendations,
} from './lib/recommendations'
import {
  collectSeriesReleases,
  comingSoonSeriesNames,
  computeSeriesGaps,
  incompleteSeriesNames,
  nextInSeries,
} from './lib/seriesGaps'
import {
  dedupeReleaseItems,
  releaseDedupeKey,
  seriesEntryToItem,
  type ReleaseItem,
} from './lib/releases'
import {
  EMPTY_NEW_RELEASES,
  fetchNewReleases,
  type NewReleases,
} from './lib/newReleases'
import {
  cachedDismissedNews,
  dismissedNewsSynced,
  dismissNewsItem,
  EMPTY_NEWS,
  fetchNews,
  pruneDismissedNews,
  type News,
} from './lib/news'
import { EMPTY_PROMPTS, fetchPrompts, type Prompts } from './lib/prompts'
import { EMPTY_LISTS, fetchLists, type Lists } from './lib/lists'
import {
  EMPTY_READING,
  fetchReading,
  authorAffinity,
  readingProfile,
  type Reading,
  type ReadingStatus,
} from './lib/reading'
import { loadPendingReading, queueReadingChange } from './lib/readingQueue'
import { FINISHED_FRACTION, getProgress } from './lib/readingProgress'
import { ReadNext } from './components/ReadNext'
import { clearSentTracker, getSentMap, markSent, unmarkSent } from './lib/sentTracker'
import {
  clearSettings,
  loadPartialSettings,
  loadSettings,
  saveSettings,
  type KoboDevice,
  type ViewerSettings,
} from './lib/settings'
import { getViewerName, setViewerName } from './lib/viewerIdentity'
import { fetchEmbeddings, type Embeddings } from './lib/embeddings'
import { getSearchMode, setSearchMode, type SearchMode } from './lib/searchMode'
import { ensureModel, isModelLoaded, semanticSearch, type ScoredHit } from './lib/semanticSearch'
import { addToWishlist } from './lib/wishlist'
import { useLibrary } from './hooks/useLibrary'

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// A share link (?clientId=...&folderId=...) points a device at a specific
// library with no setup form. Rarely needed now that the deployed build
// ships working defaults (see lib/config.ts) — kept for pointing a device
// at a different library/client. Consumed once on load, then scrubbed from
// the address bar so the values don't linger in browser history.
function consumeSharedSettings(): ViewerSettings | null {
  const params = new URLSearchParams(window.location.search)
  const googleClientId = params.get('clientId')
  const libraryFolderId = params.get('folderId')
  if (!googleClientId || !libraryFolderId) return null

  const shared: ViewerSettings = { googleClientId, libraryFolderId }
  saveSettings(shared)
  window.history.replaceState({}, '', window.location.pathname)
  return shared
}

export default function App() {
  const [viewerName, setViewerNameState] = useState(getViewerName)
  const [showSetup, setShowSetup] = useState(false)
  const [showDevices, setShowDevices] = useState(false)
  const [showWishlist, setShowWishlist] = useState(false)
  const [showActivity, setShowActivity] = useState(false)
  const [editingSettings, setEditingSettings] = useState(false)
  const [settings, setSettings] = useState<ViewerSettings | null>(
    () => consumeSharedSettings() ?? loadSettings(),
  )
  const [shareStatus, setShareStatus] = useState<string | null>(null)

  const lib = useLibrary(settings)
  const { token, files, index } = lib

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('title')
  // Keyword vs. meaning (semantic) search. prompts/29.
  const [searchMode, setSearchModeState] = useState<SearchMode>(getSearchMode)
  const [semanticHits, setSemanticHits] = useState<ScoredHit[] | null>(null)
  const [semanticBusy, setSemanticBusy] = useState(false)
  const [semanticError, setSemanticError] = useState<string | null>(null)
  const embeddingsRef = useRef<Embeddings | null>(null)
  const [filter, setFilter] = useState<FilterKey>('all')
  const [showAll, setShowAll] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  // "Readers also liked" data — a separate, larger sidecar, so it's only
  // fetched the first time someone expands a row.
  const [recommendations, setRecommendations] = useState<Recommendations>({})
  const recsLoadedRef = useRef(false)
  // The release strip cover that's been clicked — opens <ReleaseCard>.
  const [releaseCardItem, setReleaseCardItem] = useState<ReleaseItem | null>(null)
  // "Full screen ⤢" on any strip opens one overlay showing all of them.
  const [stripsFullscreen, setStripsFullscreen] = useState(false)
  // prompts/27 Part 2 — the "From authors you read" sidecar, fetched lazily.
  const [newReleases, setNewReleases] = useState<NewReleases>(EMPTY_NEW_RELEASES)
  const newReleasesLoadedRef = useRef(false)
  const [showNewReleases, setShowNewReleases] = useState(false)
  // prompts/32 — the SFF news sidecar, fetched lazily with the others.
  const [news, setNews] = useState<News>(EMPTY_NEWS)
  const [dismissedNews, setDismissedNews] = useState<Set<string>>(cachedDismissedNews)
  const [showNewsScreen, setShowNewsScreen] = useState(false)
  const [showStats, setShowStats] = useState(false)
  const [prompts, setPrompts] = useState<Prompts>(EMPTY_PROMPTS)
  const [showPromptsScreen, setShowPromptsScreen] = useState(false)
  const [lists, setLists] = useState<Lists>(EMPTY_LISTS)
  // prompts/30 — the owner's Hardcover reading status, fetched lazily
  // alongside the new-releases sidecar. `pendingReading` overlays changes
  // made here that haven't synced to Hardcover yet (Phase 3).
  const [reading, setReading] = useState<Reading>(EMPTY_READING)
  const [pendingReading, setPendingReading] = useState<Map<string, ReadingStatus>>(new Map())
  const [readingBookId, setReadingBookId] = useState<string | null>(null)
  // Bumped when the reader closes so the "Continue reading" strip re-reads
  // the (localStorage-backed) reading progress.
  const [progressTick, setProgressTick] = useState(0)
  const [offlineCount, setOfflineCount] = useState(0)
  const [pendingScrollId, setPendingScrollId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [sendingToKobo, setSendingToKobo] = useState(false)
  const [koboError, setKoboError] = useState<string | null>(null)
  const [koboMessage, setKoboMessage] = useState<string | null>(null)
  // Per-row, per-device send state (keyed by sendKey) — lets each "→ Tess"
  // button show its own "Sending…"/failed state immediately, so a slow or
  // failed send is obvious right at the button instead of only in the
  // page-level banner below, which is easy to miss (and easy to mistake
  // for "nothing happened" — the exact thing that led to someone clicking
  // the same button three times in a row).
  const [sendState, setSendState] = useState<Record<string, SendStatus>>({})
  const [sentMap, setSentMap] = useState(getSentMap)

  // Drive-synced (lib.remoteKoboDevices) once a sync has resolved it —
  // falls back to this browser's own local copy until then, or if the
  // library has no synced settings file at all yet.
  const koboDevices = lib.remoteKoboDevices ?? settings?.koboDevices ?? []
  const hasKobo = koboDevices.length > 0

  function logDownload(file: DriveFile) {
    if (!token || !settings || !viewerName) return
    void logActivity(token, settings.libraryFolderId, viewerName, 'download', file.name)
  }

  function logKoboSend(file: DriveFile, device: KoboDevice) {
    if (!token || !settings || !viewerName) return
    void logActivity(token, settings.libraryFolderId, viewerName, 'kobo-send', `${file.name} → ${device.label}`)
  }

  useEffect(() => {
    void cacheStats().then((s) => setOfflineCount(s.count))
  }, [progressTick])

  useEffect(() => {
    if (!stripsFullscreen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setStripsFullscreen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [stripsFullscreen])

  // Fetch "readers also liked" the first time a row is expanded, not up front.
  useEffect(() => {
    if (!expandedId || recsLoadedRef.current || !token || !settings) return
    let cancelled = false
    void fetchRecommendations(token, settings.libraryFolderId).then((r) => {
      if (cancelled) return
      setRecommendations(r)
      if (Object.keys(r).length > 0) recsLoadedRef.current = true
    })
    return () => {
      cancelled = true
    }
  }, [expandedId, token, settings])

  // Fetch the "new & upcoming" sidecar once, on first idle — never blocking
  // first paint. It's small and its own file, like recommendations.
  useEffect(() => {
    if (newReleasesLoadedRef.current || !token || !settings) return
    newReleasesLoadedRef.current = true
    const folderId = settings.libraryFolderId
    const ric = (window as unknown as { requestIdleCallback?: (cb: () => void) => void })
      .requestIdleCallback
    const run = () => {
      void fetchNewReleases(token, folderId).then(setNewReleases)
      void fetchReading(token, folderId).then(setReading)
      void loadPendingReading(token, folderId).then(setPendingReading)
      void Promise.all([fetchNews(token, folderId), dismissedNewsSynced(token, folderId)]).then(
        ([n, dismissed]) => {
          setNews(n)
          const links = n.items.map((i) => i.link)
          void pruneDismissedNews(token, folderId, dismissed, links).then(setDismissedNews)
        },
      )
      void fetchPrompts(token, folderId).then(setPrompts)
      void fetchLists(token, folderId).then(setLists)
    }
    if (ric) ric(run)
    else setTimeout(run, 1200)
  }, [token, settings])

  // The reading data the UI sees: Hardcover's, with any locally-queued
  // (not-yet-synced) change layered on top and flagged `pending`.
  const mergedReading = useMemo<Reading>(() => {
    if (pendingReading.size === 0) return reading
    const books = { ...reading.books }
    for (const [id, status] of pendingReading) {
      books[id] = { ...(books[id] ?? { status: null }), status, pending: true }
    }
    return { ...reading, books }
  }, [reading, pendingReading])

  const allRows = useMemo(
    () => buildRows(files ?? [], index, mergedReading),
    [files, index, mergedReading],
  )

  async function markReadingStatus(row: (typeof allRows)[number], status: ReadingStatus) {
    if (!token || !settings) return
    setPendingReading((m) => new Map(m).set(row.id, status))
    try {
      await queueReadingChange(token, settings.libraryFolderId, {
        driveFileId: row.id,
        isbn13: row.isbn,
        title: row.title,
        author: row.author,
        status,
        at: new Date().toISOString(),
        by: viewerName ?? '',
      })
      if (status === 'read' && viewerName) {
        void logActivity(
          token,
          settings.libraryFolderId,
          viewerName,
          'read',
          row.author ? `${row.title} — ${row.author}` : row.title,
        )
      }
    } catch {
      setPendingReading((m) => {
        const next = new Map(m)
        next.delete(row.id)
        return next
      })
    }
  }

  // prompts/31 Part I — push the reader's position to Hardcover (advance-only,
  // the backend guards against going backwards). Fire-and-forget on reader close.
  function pushReadingProgress(row: (typeof allRows)[number], percent: number) {
    if (!token || !settings) return
    const known = row.reading?.progress ?? 0
    if (percent <= 0.02 || percent >= 0.98 || percent - known < 0.05) return
    void queueReadingChange(token, settings.libraryFolderId, {
      driveFileId: row.id,
      isbn13: row.isbn,
      title: row.title,
      author: row.author,
      progressPercent: Math.round(percent * 1000) / 1000,
      at: new Date().toISOString(),
      by: viewerName ?? '',
    })
  }

  // prompts/32 — X an SFF-news article. Hidden for good and synced to the
  // Drive sidecar; the slot fills from the rest of the 50-item pool. The lib
  // serialises the sidecar writes + merges through the cache, so a slightly
  // stale `dismissedNews` here is fine.
  function dismissNews(link: string) {
    setDismissedNews((d) => new Set(d).add(link))
    if (token && settings) {
      void dismissNewsItem(token, settings.libraryFolderId, link, dismissedNews).then(
        setDismissedNews,
      )
    }
  }

  async function requestBook(rec: RecBook) {
    if (!token || !settings || !viewerName) return 'already-listed' as const
    const result = await addToWishlist(
      token,
      settings.libraryFolderId,
      { title: rec.title, author: rec.author, series: null, isbn13: rec.isbn13, cover: null, year: null },
      viewerName,
      allRows,
    )
    if (result === 'added') {
      void logActivity(
        token,
        settings.libraryFolderId,
        viewerName,
        'request',
        rec.author ? `${rec.title} — ${rec.author}` : rec.title,
      )
    }
    return result
  }

  async function requestRelease(item: ReleaseItem) {
    if (!token || !settings || !viewerName) return 'already-listed' as const
    const result = await addToWishlist(
      token,
      settings.libraryFolderId,
      {
        title: item.title,
        author: item.author,
        series: item.series,
        isbn13: item.isbn13,
        cover: null,
        year: null,
      },
      viewerName,
      allRows,
    )
    if (result === 'added') {
      void logActivity(
        token,
        settings.libraryFolderId,
        viewerName,
        'request',
        item.author ? `${item.title} — ${item.author}` : item.title,
      )
    }
    return result
  }

  const recentBooks = useMemo(() => pickRecentBooks(allRows), [allRows])
  const seriesGaps = useMemo(
    () => computeSeriesGaps(allRows, index.series),
    [allRows, index.series],
  )
  const incompleteSeries = useMemo(() => incompleteSeriesNames(seriesGaps), [seriesGaps])
  const comingSoonSeries = useMemo(() => comingSoonSeriesNames(seriesGaps), [seriesGaps])
  // prompts/30 Phase 2 — "Read next" strip + author-read-count weighting for
  // the release strips.
  const readNext = useMemo(() => nextInSeries(allRows), [allRows])
  const authorReadCounts = useMemo(
    () => readingProfile(reading, new Map(allRows.map((r) => [r.id, { author: r.author }]))),
    [reading, allRows],
  )
  // prompts/31 Part B — your mean rating per author, to re-rank "readers also
  // liked". Same row join as authorReadCounts.
  const authorAffinityMap = useMemo(
    () => authorAffinity(reading, new Map(allRows.map((r) => [r.id, { author: r.author }]))),
    [reading, allRows],
  )
  // prompts/27 Part 1 — "New / Coming soon in your series" strips, flattened
  // straight out of the per-series Hardcover catalogues already in the index.
  const seriesReleases = useMemo(() => collectSeriesReleases(seriesGaps), [seriesGaps])
  // The feed powering the two release strips + <NewReleasesScreen>: series
  // entries (viewer-derived) merged with the author sidecar, then sorted by
  // how-much-you-read-the-author (bucketed 0/1/2/3+, so a stale release from a
  // favourite author floats up without fully reordering the list) then date.
  const recentReleaseFeed = useMemo(() => {
    const tier = (i: ReleaseItem) => Math.min(authorReadCounts.get(i.author ?? '') ?? 0, 3)
    return dedupeReleaseItems(
      seriesReleases.recent.map(seriesEntryToItem),
      newReleases.recent,
    ).sort((a, b) => tier(b) - tier(a) || (b.releaseDate ?? '').localeCompare(a.releaseDate ?? ''))
  }, [seriesReleases, newReleases, authorReadCounts])
  const upcomingReleaseFeed = useMemo(() => {
    const tier = (i: ReleaseItem) => Math.min(authorReadCounts.get(i.author ?? '') ?? 0, 3)
    return dedupeReleaseItems(
      seriesReleases.upcoming.map(seriesEntryToItem),
      newReleases.upcoming,
    ).sort((a, b) => tier(b) - tier(a) || (a.releaseDate ?? '').localeCompare(b.releaseDate ?? ''))
  }, [seriesReleases, newReleases, authorReadCounts])
  // Part 3 — "Most anticipated" (opt-in): Hardcover's overall top upcoming
  // books, minus anything already in the library-filtered feeds above.
  const globalReleaseFeed = useMemo(() => {
    if (!settings?.showGlobalReleases) return []
    const known = new Set(
      [...recentReleaseFeed, ...upcomingReleaseFeed].map(releaseDedupeKey),
    )
    return newReleases.global
      .filter((i) => !known.has(releaseDedupeKey(i)))
      .sort((a, b) => (a.releaseDate ?? '').localeCompare(b.releaseDate ?? ''))
  }, [settings, newReleases, recentReleaseFeed, upcomingReleaseFeed])
  // prompts/31 Part G — "Trending on Hardcover" (opt-in). Whole list, minus
  // anything already in the feeds above; owned books keep their spot and get
  // an "In library" badge on the card.
  const trendingFeed = useMemo(() => {
    if (!settings?.showTrending) return []
    const known = new Set(
      [...recentReleaseFeed, ...upcomingReleaseFeed, ...globalReleaseFeed].map(releaseDedupeKey),
    )
    return newReleases.trending.filter((i) => !known.has(releaseDedupeKey(i)))
  }, [settings, newReleases, recentReleaseFeed, upcomingReleaseFeed, globalReleaseFeed])
  const genreFacets = useMemo(() => topGenres(allRows), [allRows])
  const moodFacets = useMemo(() => topMoods(allRows), [allRows])

  // Meaning mode: run the semantic search a beat after typing settles. The
  // first call downloads the model (~23 MB, then cached); later ones are
  // instant. A miss (no sidecar, model failed) falls back to a message, not
  // keyword results — the two modes stay distinct.
  const runSemanticSearch = useMemo(
    () => async (q: string) => {
      if (!token || !settings || q.trim().length < 2) {
        setSemanticHits(null)
        return
      }
      setSemanticBusy(true)
      setSemanticError(null)
      try {
        if (!embeddingsRef.current) {
          embeddingsRef.current = await fetchEmbeddings(token, settings.libraryFolderId)
        }
        const emb = embeddingsRef.current
        if (!emb) {
          setSemanticError('Meaning search isn’t set up for this library yet.')
          setSemanticHits(null)
          return
        }
        setSemanticHits(await semanticSearch(q.trim(), emb))
      } catch {
        setSemanticError('The search model didn’t load — try again, or switch to Keyword.')
        setSemanticHits(null)
      } finally {
        setSemanticBusy(false)
      }
    },
    [token, settings],
  )

  useEffect(() => {
    if (searchMode !== 'meaning') return
    if (query.trim().length < 2) {
      setSemanticHits(null)
      setSemanticError(null)
      return
    }
    const id = setTimeout(() => void runSemanticSearch(query), 400)
    return () => clearTimeout(id)
  }, [query, searchMode, runSemanticSearch])

  const semanticScores = useMemo(
    () =>
      searchMode === 'meaning' && semanticHits
        ? new Map(semanticHits.map((h) => [h.id, h.score]))
        : null,
    [searchMode, semanticHits],
  )

  const rows = useMemo(() => {
    if (semanticScores) {
      const q = query.trim().toLowerCase()
      const hits = allRows
        .filter(
          (r) =>
            semanticScores.has(r.id) &&
            matchesFilter(r, filter, sentMap, incompleteSeries, comingSoonSeries),
        )
        .map((r) => ({
          r,
          score: semanticScores.get(r.id)!,
          exact:
            q.length > 0 &&
            `${r.title} ${r.author ?? ''} ${r.series ?? ''}`.toLowerCase().includes(q),
        }))
      hits.sort((a, b) => Number(b.exact) - Number(a.exact) || b.score - a.score)
      return hits.map((h) => h.r)
    }
    const out = allRows.filter(
      (row) =>
        matchesRow(row, query) &&
        matchesFilter(row, filter, sentMap, incompleteSeries, comingSoonSeries),
    )
    out.sort(SORTS[sort])
    // prompts/31 Part B — in the want-to-read view, float the "quick wins"
    // (short + highly rated) to the top; the chosen sort stays the tiebreak.
    if (filter === 'want') {
      out.sort((a, b) => quickWinScore(b) - quickWinScore(a) || SORTS[sort](a, b))
    }
    return out
  }, [allRows, query, sort, filter, sentMap, incompleteSeries, comingSoonSeries, semanticScores])

  function switchSearchMode(mode: SearchMode) {
    setSearchMode(mode)
    setSearchModeState(mode)
    setSemanticHits(null)
    setSemanticError(null)
    if (mode === 'meaning') void ensureModel().catch(() => {})
  }

  // Log a search a beat after typing settles, not on every keystroke — a
  // read+write to Drive per character would be both wasteful and racy.
  const lastLoggedQueryRef = useRef('')
  useEffect(() => {
    if (!token || !settings || !viewerName) return
    const trimmed = query.trim()
    if (trimmed.length < 2 || trimmed === lastLoggedQueryRef.current) return
    const folderId = settings.libraryFolderId
    const id = setTimeout(() => {
      lastLoggedQueryRef.current = trimmed
      void logActivity(token, folderId, viewerName, 'search', trimmed)
    }, 800)
    return () => clearTimeout(id)
  }, [query, token, viewerName, settings])

  function selectMany(ids: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }

  function filterTo(value: string, by: SortKey) {
    setQuery(value)
    setSort(by)
    setFilter('all')
    window.scrollTo({ top: 0 })
  }

  function filterToGenre(genre: string) {
    setQuery('')
    setFilter(`genre:${genre}`)
    window.scrollTo({ top: 0 })
  }

  function filterToMood(mood: string) {
    setQuery('')
    setFilter(`mood:${mood}`)
    window.scrollTo({ top: 0 })
  }

  // A cover in the "recently added" ticker was clicked — switch the list to
  // the recently-added view (where the book is near the top), open it, and
  // scroll it into view once it's rendered.
  function jumpToRecent(id: string) {
    setQuery('')
    setFilter('all')
    setSort('added')
    setShowAll(true)
    setExpandedId(id)
    setPendingScrollId(id)
  }

  useEffect(() => {
    if (!pendingScrollId) return
    const el = document.getElementById(`book-${pendingScrollId}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setPendingScrollId(null)
  }, [pendingScrollId, rows])

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function shareLink(): { link: string; message: string } | null {
    if (!settings) return null
    const link = `${window.location.origin}${window.location.pathname}?clientId=${encodeURIComponent(settings.googleClientId)}&folderId=${encodeURIComponent(settings.libraryFolderId)}`
    return {
      link,
      message: `You're invited to browse and download books from the BookBrain library. Open this link and sign in with your Google account:\n${link}`,
    }
  }

  async function handleShare() {
    const share = shareLink()
    if (!share) return
    if (navigator.share) {
      try {
        await navigator.share({ title: 'BookBrain Library', text: share.message })
      } catch {
        /* cancelled */
      }
      return
    }
    await handleCopyLink()
  }

  async function handleCopyLink() {
    const share = shareLink()
    if (!share) return
    try {
      await navigator.clipboard.writeText(share.message)
      setShareStatus('Copied! Paste it into a message to send.')
    } catch {
      setShareStatus(`Copy failed — here's the link: ${share.link}`)
    }
    setTimeout(() => setShareStatus(null), 5000)
  }

  function handleForget() {
    clearSettings()
    clearSentTracker()
    lib.reset()
    setSentMap({})
    setSettings(null)
  }

  function handleDeviceRemoval(folderId: string, filename: string) {
    const match = allRows.find((r) => r.filename === filename)
    if (match) setSentMap(unmarkSent(folderId, [match.id]))
  }

  // The device screen reports each folder's real contents; sync the local
  // "✓ on device" ticks to match — add present books, drop absent ones,
  // matched by filename (the copy keeps the library file's name).
  function handleDeviceReconcile(folderId: string, filenames: string[]) {
    const present = new Set(filenames)
    const shouldBeSent = allRows.filter((r) => present.has(r.filename)).map((r) => r.id)
    const staleIds = Object.keys(sentMap[folderId] ?? {}).filter((id) => {
      const row = allRows.find((r) => r.id === id)
      return row ? !present.has(row.filename) : false
    })
    let next = markSent(folderId, shouldBeSent)
    if (staleIds.length > 0) next = unmarkSent(folderId, staleIds)
    setSentMap(next)
  }

  async function handleDownloadSelected() {
    if (!token) return
    setDownloading(true)
    setDownloadError(null)
    const toDownload = allRows.filter((r) => selected.has(r.id))
    const stillFailed = new Set<string>()
    for (const { file } of toDownload) {
      try {
        await downloadFile(token, file)
        logDownload(file)
      } catch (err) {
        lib.flagAuthError(err)
        stillFailed.add(file.id)
      }
      await sleep(300)
    }
    setSelected(stillFailed)
    setDownloadError(
      stillFailed.size > 0
        ? `${stillFailed.size} of ${toDownload.length} download${toDownload.length === 1 ? '' : 's'} failed — still selected, try again.`
        : null,
    )
    setDownloading(false)
  }

  async function sendToKobo(file: DriveFile, device: KoboDevice) {
    if (!token) return
    const key = sendKey(file.id, device.folderId)
    if (sendState[key] === 'pending') return // already in flight — the disabled button should prevent this anyway
    setKoboError(null)
    setKoboMessage(null)
    setSendState((prev) => ({ ...prev, [key]: 'pending' }))
    try {
      await copyFileToFolder(token, file, device.folderId)
      logKoboSend(file, device)
      setSentMap(markSent(device.folderId, [file.id]))
      setKoboMessage(`Sent "${file.name}" to ${device.label}.`)
      setSendState((prev) => {
        const { [key]: _removed, ...rest } = prev
        return rest
      })
    } catch (err) {
      lib.flagAuthError(err)
      setKoboError(err instanceof Error ? err.message : `Failed to send to ${device.label}.`)
      setSendState((prev) => ({ ...prev, [key]: 'error' }))
      // Leave the button showing "Failed" for a few seconds rather than
      // instantly reverting to its normal label — long enough to register
      // as feedback, short enough that a genuine retry isn't stuck looking
      // like a stale error.
      setTimeout(() => {
        setSendState((prev) => {
          if (prev[key] !== 'error') return prev
          const { [key]: _removed, ...rest } = prev
          return rest
        })
      }, 4000)
    }
  }

  async function handleSendSelectedToKobo(device: KoboDevice) {
    if (!token) return
    setSendingToKobo(true)
    setKoboError(null)
    setKoboMessage(null)
    const toSend = allRows.filter((r) => selected.has(r.id))
    const sentIds: string[] = []
    const stillFailed = new Set<string>()
    for (const { file } of toSend) {
      try {
        await copyFileToFolder(token, file, device.folderId)
        logKoboSend(file, device)
        sentIds.push(file.id)
      } catch (err) {
        lib.flagAuthError(err)
        stillFailed.add(file.id)
      }
    }
    if (sentIds.length > 0) setSentMap(markSent(device.folderId, sentIds))
    setSelected(stillFailed)
    setKoboMessage(`Sent ${sentIds.length} book${sentIds.length === 1 ? '' : 's'} to ${device.label}.`)
    setKoboError(
      stillFailed.size > 0
        ? `${stillFailed.size} of ${toSend.length} failed — still selected, try again.`
        : null,
    )
    setSendingToKobo(false)
  }

  if (!viewerName) {
    return (
      <WhoAmI
        onPick={(name) => {
          setViewerName(name)
          setViewerNameState(name)
        }}
      />
    )
  }

  if (showSetup) return <SetupChecklist onBack={() => setShowSetup(false)} />

  if (!settings || editingSettings) {
    return (
      <>
        <SettingsForm
          // Prefer whatever's synced from Drive for Kobo devices over this
          // browser's own (possibly empty, possibly stale) local copy, once
          // a sync has actually resolved one — otherwise re-opening
          // Settings on a second device would show blank rows even though
          // sending books already works. `lib.remoteKoboDevices` is null
          // (not [] ?? []) until that first sync completes, so this only
          // overrides once there's something real to show.
          initial={{
            ...loadPartialSettings(),
            ...(lib.remoteKoboDevices ? { koboDevices: lib.remoteKoboDevices } : {}),
          }}
          onSave={(s) => {
            const resyncNeeded =
              settings?.googleClientId !== s.googleClientId ||
              settings?.libraryFolderId !== s.libraryFolderId
            saveSettings(s)
            setSettings(s)
            setEditingSettings(false)
            if (resyncNeeded) lib.reset()
            // Already signed in (editing, not first-time setup) — push the
            // Kobo device list to Drive right away rather than waiting for
            // the next sync, so another device picks up the change sooner.
            else lib.saveKoboDevices(s.koboDevices ?? [])
          }}
          onCancel={settings ? () => setEditingSettings(false) : undefined}
        />
        <p className="mx-auto max-w-md p-6 pt-0 text-center">
          <button className="text-xs text-neutral-400 underline" onClick={() => setShowSetup(true)}>
            Lost your hard drive? Recovery checklist
          </button>
        </p>
      </>
    )
  }

  if (!token) {
    return (
      <div className="mx-auto max-w-sm px-6 pt-24 pb-12 text-center">
        <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="mx-auto h-12 w-12" />
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">BookBrain Library</h1>
        <p className="mt-2 text-sm text-neutral-500">Sign in with Google to browse your library.</p>
        <button
          className="btn btn-primary mx-auto mt-6 px-4 py-2 text-sm"
          disabled={lib.signingIn}
          onClick={lib.signIn}
        >
          {lib.signingIn ? 'Signing in…' : 'Sign in with Google'}
        </button>
        {lib.authError && <p className="mt-3 text-sm text-red-600">{lib.authError}</p>}
        <p className="mt-6 text-xs leading-relaxed text-neutral-400">
          Sending books to a Kobo copies files between Drive folders, which Google only allows with
          full Drive access — so signing in grants this page access to your Drive, not just the
          library folder. Nothing is saved anywhere but Google: closing or reloading this page signs
          you out.
        </p>
        <button
          className="mt-8 text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
          onClick={() => setShowSetup(true)}
        >
          Lost your hard drive? Recovery checklist
        </button>
      </div>
    )
  }

  if (readingBookId && token) {
    const readingBook = allRows.find((r) => r.id === readingBookId)
    if (readingBook) {
      return (
        <Reader
          token={token}
          book={readingBook}
          onAuthError={lib.flagAuthError}
          onClose={() => {
            const p = getProgress(readingBook.id)
            if (p && p.percent >= FINISHED_FRACTION && readingBook.reading?.status !== 'read') {
              // Finished the book in the reader → mark it read on Hardcover too.
              void markReadingStatus(readingBook, 'read')
            } else if (p) {
              // Otherwise push the position forward (Part I, advance-only).
              pushReadingProgress(readingBook, p.percent)
            }
            setReadingBookId(null)
            setProgressTick((t) => t + 1)
          }}
        />
      )
    }
  }

  if (showActivity) {
    return (
      <ActivityScreen
        token={token}
        libraryFolderId={settings.libraryFolderId}
        onBack={() => setShowActivity(false)}
      />
    )
  }

  if (showDevices) {
    return (
      <DeviceLibrary
        token={token}
        devices={koboDevices}
        onBack={() => setShowDevices(false)}
        onRemoved={handleDeviceRemoval}
        onReconcile={handleDeviceReconcile}
      />
    )
  }

  if (showNewReleases) {
    return (
      <NewReleasesScreen
        recent={recentReleaseFeed}
        upcoming={upcomingReleaseFeed}
        allRows={allRows}
        onRequest={requestRelease}
        onBack={() => setShowNewReleases(false)}
      />
    )
  }

  if (showNewsScreen) {
    return (
      <NewsScreen
        news={news}
        dismissed={dismissedNews}
        onDismiss={dismissNews}
        onBack={() => setShowNewsScreen(false)}
      />
    )
  }

  if (showStats) {
    return (
      <ReadingStatsScreen
        reading={mergedReading}
        rows={allRows}
        onBack={() => setShowStats(false)}
      />
    )
  }

  if (showPromptsScreen) {
    return (
      <PromptsScreen
        prompts={prompts}
        rows={allRows}
        token={token}
        onBack={() => setShowPromptsScreen(false)}
        onOpen={(row) => {
          setShowPromptsScreen(false)
          jumpToRecent(row.id)
        }}
      />
    )
  }

  if (showWishlist) {
    return (
      <WishlistScreen
        token={token}
        libraryFolderId={settings.libraryFolderId}
        rows={allRows}
        viewerName={viewerName}
        wantCandidates={reading.wantUnowned}
        listCandidates={lists.candidates}
        onBack={() => setShowWishlist(false)}
      />
    )
  }

  const filterChips: { key: FilterKey; label: string }[] = [
    { key: 'all', label: 'All' },
    ...koboDevices.map((d) => ({ key: `on:${d.folderId}` as FilterKey, label: `On ${d.label}` })),
    ...(incompleteSeries.size > 0 ? [{ key: 'gaps' as FilterKey, label: 'Missing books' }] : []),
    ...(comingSoonSeries.size > 0
      ? [{ key: 'comingsoon' as FilterKey, label: 'Coming soon' }]
      : []),
    ...(Object.keys(reading.books).length > 0
      ? [
          { key: 'read' as FilterKey, label: 'Read' },
          { key: 'unread' as FilterKey, label: 'Unread' },
          ...(Object.values(reading.books).some((e) => e.status === 'want')
            ? [{ key: 'want' as FilterKey, label: 'Want to read' }]
            : []),
          ...(Object.values(reading.books).some((e) => (e.rating ?? 0) >= 4)
            ? [{ key: 'favourites' as FilterKey, label: '★ Favourites' }]
            : []),
        ]
      : []),
    ...genreFacets.map((g) => ({ key: `genre:${g}` as FilterKey, label: g })),
    ...moodFacets.map((m) => ({ key: `mood:${m}` as FilterKey, label: m })),
  ]

  const koboStatus = koboError || koboMessage
  const emptyMessage =
    (files?.length ?? 0) === 0 ? 'No books found in this folder.' : 'Nothing matches.'

  // Don't dump the whole library on screen — wait for a search, a filter,
  // or an explicit "show everything".
  const browsing = query.trim() !== '' || filter !== 'all' || showAll

  return (
    <div className="mx-auto max-w-2xl px-4 pb-10 sm:px-6">
      <LibraryHeader
        busy={lib.syncing || lib.loading}
        hasKobo={hasKobo}
        onRefresh={lib.refresh}
        onRebuild={lib.rebuild}
        onShowDevices={() => setShowDevices(true)}
        onShowWishlist={() => setShowWishlist(true)}
        onShowActivity={() => setShowActivity(true)}
        onShowNews={() => setShowNewsScreen(true)}
        onShowStats={
          Object.keys(reading.books).length > 0 || reading.goal
            ? () => setShowStats(true)
            : undefined
        }
        onShowPrompts={
          prompts.prompts.length > 0 ? () => setShowPromptsScreen(true) : undefined
        }
        onShare={handleShare}
        onCopyLink={handleCopyLink}
        onEditSettings={() => setEditingSettings(true)}
        onShowSetup={() => setShowSetup(true)}
        onForget={handleForget}
        offlineCount={offlineCount}
        onClearDownloads={() => {
          void clearBookCache().then(() => setOfflineCount(0))
        }}
      />

      {!lib.loading && mergedReading.goal && (
        <ReadingGoalBar goal={mergedReading.goal} onOpen={() => setShowStats(true)} />
      )}

      {!lib.loading && (
        <>
          <RecentMarquee
            books={recentBooks}
            token={token}
            onPick={jumpToRecent}
            onOpenFullscreen={() => setStripsFullscreen(true)}
          />
          <ReleaseMarquee
            label="New for you"
            items={recentReleaseFeed}
            onPick={setReleaseCardItem}
            onOpenFullscreen={() => setStripsFullscreen(true)}
          />
          <ReleaseMarquee
            label="Coming soon"
            items={upcomingReleaseFeed}
            minCards={1}
            onPick={setReleaseCardItem}
            onOpenFullscreen={() => setStripsFullscreen(true)}
          />
          <ReleaseMarquee
            label="Most anticipated"
            items={globalReleaseFeed}
            onPick={setReleaseCardItem}
            onOpenFullscreen={() => setStripsFullscreen(true)}
          />
          <ReleaseMarquee
            label="Trending on Hardcover"
            items={trendingFeed}
            onPick={setReleaseCardItem}
            onOpenFullscreen={() => setStripsFullscreen(true)}
          />
          {recentReleaseFeed.length + upcomingReleaseFeed.length > 0 && (
            <button
              type="button"
              className="mb-3 -mt-1 text-[11px] text-neutral-400 hover:text-brand-600 dark:hover:text-brand-400"
              onClick={() => setShowNewReleases(true)}
            >
              See all new &amp; upcoming →
            </button>
          )}
        </>
      )}

      {stripsFullscreen && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-neutral-950/95 backdrop-blur">
          <div className="flex min-h-full flex-col items-center justify-center gap-8 px-2 py-12">
            <div className="w-full max-w-5xl space-y-8">
              <RecentMarquee
                books={recentBooks}
                token={token}
                fullscreen
                onPick={(id) => {
                  setStripsFullscreen(false)
                  jumpToRecent(id)
                }}
              />
              <ReleaseMarquee
                label="New for you"
                items={recentReleaseFeed}
                fullscreen
                onPick={(item) => {
                  setStripsFullscreen(false)
                  setReleaseCardItem(item)
                }}
              />
              <ReleaseMarquee
                label="Coming soon"
                items={upcomingReleaseFeed}
                minCards={1}
                fullscreen
                onPick={(item) => {
                  setStripsFullscreen(false)
                  setReleaseCardItem(item)
                }}
              />
              <ReleaseMarquee
                label="Most anticipated"
                items={globalReleaseFeed}
                fullscreen
                onPick={(item) => {
                  setStripsFullscreen(false)
                  setReleaseCardItem(item)
                }}
              />
              <ReleaseMarquee
                label="Trending on Hardcover"
                items={trendingFeed}
                fullscreen
                onPick={(item) => {
                  setStripsFullscreen(false)
                  setReleaseCardItem(item)
                }}
              />
            </div>
            <button
              type="button"
              className="btn btn-neutral"
              onClick={() => setStripsFullscreen(false)}
            >
              Close (Esc)
            </button>
          </div>
        </div>
      )}

      {releaseCardItem && (
        <ReleaseCard
          item={releaseCardItem}
          onRequest={requestRelease}
          onClose={() => setReleaseCardItem(null)}
        />
      )}

      {!lib.loading && token && (
        <ContinueReading
          rows={allRows}
          token={token}
          tick={progressTick}
          onRead={(id) => setReadingBookId(id)}
        />
      )}

      {!lib.loading && token && (
        <ReadNext
          items={readNext}
          token={token}
          onRead={(id) => setReadingBookId(id)}
          onOpen={jumpToRecent}
        />
      )}

      {!lib.loading && settings?.showNews !== false && news.items.length > 0 && (
        <NewsFeed
          items={news.items}
          dismissed={dismissedNews}
          onDismiss={dismissNews}
          onSeeAll={() => setShowNewsScreen(true)}
        />
      )}

      {lib.sessionExpired && (
        <div className="mb-2 flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-300">
          <span className="flex-1">Your Google session expired.</span>
          <button className="btn btn-primary btn-xs" onClick={lib.signIn}>
            Reconnect
          </button>
        </div>
      )}

      <div className="sticky top-0 z-20 bg-neutral-50/95 py-2 backdrop-blur dark:bg-neutral-950/95">
        {selected.size > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
            <span className="font-medium">{selected.size} selected</span>
            <button className="btn btn-neutral" onClick={() => setSelected(new Set())}>
              Clear
            </button>
            <span className="mx-1 hidden h-4 w-px bg-neutral-200 sm:block dark:bg-neutral-700" />
            <button className="btn btn-primary" disabled={downloading} onClick={handleDownloadSelected}>
              {downloading ? 'Downloading…' : `Download ${selected.size}`}
            </button>
            {koboDevices.map((device) => (
              <button
                key={device.folderId}
                className="btn btn-neutral"
                disabled={sendingToKobo}
                onClick={() => handleSendSelectedToKobo(device)}
              >
                {sendingToKobo ? 'Sending…' : `Send ${selected.size} to ${device.label}`}
              </button>
            ))}
            {downloadError && <span className="text-xs text-red-600">{downloadError}</span>}
            {hasKobo && koboError && <span className="text-xs text-red-600">{koboError}</span>}
            {hasKobo && !koboError && koboMessage && (
              <span className="text-xs text-neutral-500">{koboMessage}</span>
            )}
          </div>
        )}
        <div className="flex gap-2">
          <div className="relative min-w-0 flex-1">
            <input
              className="field w-full"
              placeholder={
                searchMode === 'meaning'
                  ? 'Describe the book — "generation ship sci-fi"…'
                  : 'Search title, author, or series…'
              }
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="mt-1 flex items-center gap-1.5">
              {(['keyword', 'meaning'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => switchSearchMode(m)}
                  className={`rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                    searchMode === m
                      ? 'border-brand-600 bg-brand-600 text-white'
                      : 'border-neutral-300 bg-white text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:hover:bg-neutral-800'
                  }`}
                >
                  {m === 'keyword' ? 'Keyword' : '✨ Meaning'}
                </button>
              ))}
              {searchMode === 'meaning' && semanticBusy && (
                <span className="text-[11px] text-neutral-400">
                  {isModelLoaded() ? 'Searching…' : 'Loading search model (one-time ~23 MB)…'}
                </span>
              )}
              {searchMode === 'meaning' && semanticError && (
                <span className="text-[11px] text-red-500">{semanticError}</span>
              )}
            </div>
          </div>
          {searchMode === 'keyword' && (
            <select
              className="field h-min shrink-0"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              aria-label="Sort books"
            >
              {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
                <option key={key} value={key}>
                  {SORT_LABELS[key]}
                </option>
              ))}
            </select>
          )}
        </div>
        {filterChips.length > 1 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {filterChips.map((chip) => (
              <button
                key={chip.key}
                onClick={() => setFilter(chip.key)}
                className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                  filter === chip.key
                    ? 'border-brand-600 bg-brand-600 text-white'
                    : 'border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800'
                }`}
              >
                {chip.label}
              </button>
            ))}
          </div>
        )}
        {(lib.syncMessage || shareStatus) && !lib.syncing && !lib.loading && (
          <p className="mt-1.5 truncate text-xs text-neutral-400">
            {shareStatus ?? lib.syncMessage}
          </p>
        )}
        {(filter === 'read' || filter === 'want') && reading.unmatched.read > 0 && (
          <p className="mt-1.5 truncate text-xs text-neutral-400">
            {reading.reader} has read {reading.unmatched.read} book
            {reading.unmatched.read === 1 ? '' : 's'} that aren&rsquo;t in the library.
          </p>
        )}
        {filter === 'want' && (
          <p className="mt-1.5 truncate text-xs text-neutral-400">
            Quick wins first — shorter, higher-rated books up top.
          </p>
        )}
      </div>

      {lib.loading && (
        <p className="mt-6 text-sm text-neutral-500">
          Building your library for the first time — this may take a moment…
        </p>
      )}
      {lib.loadError && <p className="mt-6 text-sm text-red-600">{lib.loadError}</p>}

      {selected.size === 0 && hasKobo && koboStatus && (
        <p className={`mt-3 text-xs ${koboError ? 'text-red-600' : 'text-neutral-500'}`}>
          {koboStatus}
        </p>
      )}

      {!lib.loading && files !== null && !browsing && (
        <div className="mt-10 text-center text-sm text-neutral-400">
          <p>
            {files.length.toLocaleString()} book{files.length === 1 ? '' : 's'} in your library.
            <br />
            Search, or pick a filter, to see them.
          </p>
          <button
            className="btn btn-neutral mt-4"
            onClick={() => setShowAll(true)}
          >
            Show all books
          </button>
        </div>
      )}

      {!lib.loading && files !== null && browsing && (
        <BookList
          rows={rows}
          allRows={allRows}
          totalCount={files.length}
          sort={sort}
          ranked={semanticScores != null}
          semanticScores={semanticScores}
          reader={reading.reader}
          token={token}
          seriesGaps={seriesGaps}
          recommendations={recommendations}
          authorAffinity={authorAffinityMap}
          selected={selected}
          expandedId={expandedId}
          sentMap={sentMap}
          koboDevices={koboDevices}
          sendState={sendState}
          emptyMessage={emptyMessage}
          onToggleSelect={toggleSelected}
          onSelectMany={selectMany}
          onExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
          onSend={sendToKobo}
          onDownload={(file) =>
            downloadFile(token, file)
              .then(() => logDownload(file))
              .catch((err) => {
                lib.flagAuthError(err)
                setDownloadError(err.message)
              })
          }
          onRead={(row) => setReadingBookId(row.id)}
          onMarkRead={markReadingStatus}
          onFilterAuthor={(a) => filterTo(a, 'author')}
          onFilterSeries={(s) => filterTo(s, 'series')}
          onFilterGenre={filterToGenre}
          onFilterMood={filterToMood}
          onRequestBook={requestBook}
        />
      )}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { logActivity } from '../lib/activityLog'
import type { BookRow } from '../lib/books'
import { isAuthError } from '../lib/drive'
import { searchBooks, type BookHit } from '../lib/bookSearch'
import type { ListCandidate } from '../lib/lists'
import type { WantCandidate } from '../lib/reading'
import type { ReleaseItem } from '../lib/releases'
import {
  alreadyListed,
  EMPTY_WISHLIST,
  hitToItem,
  libraryMatch,
  loadWishlist,
  reconcile,
  saveWishlist,
  withStatus,
  type Wishlist,
  type WishlistItem,
  type WishlistStatus,
} from '../lib/wishlist'

function CoverThumb({ url, faded }: { url: string | null; faded?: boolean }) {
  return (
    <span
      className={`flex h-14 w-10 shrink-0 items-center justify-center overflow-hidden rounded bg-neutral-200 text-neutral-400 dark:bg-neutral-800 ${faded ? 'opacity-50' : ''}`}
    >
      {url ? (
        <img
          src={url}
          alt=""
          className="h-full w-full object-cover"
          onError={(e) => (e.currentTarget.style.display = 'none')}
        />
      ) : (
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" />
        </svg>
      )}
    </span>
  )
}

function whenText(iso: string): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const days = Math.floor((Date.now() - then) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days}d ago`
  return new Date(iso).toLocaleDateString()
}

const SETTABLE: { key: WishlistStatus; label: string }[] = [
  { key: 'wanted', label: 'Wanted' },
  { key: 'sourced', label: 'Sourced' },
  { key: 'declined', label: 'Declined' },
]

function StatusPicker({
  value,
  onChange,
}: {
  value: WishlistStatus
  onChange: (s: WishlistStatus) => void
}) {
  return (
    <div className="mt-1 inline-flex overflow-hidden rounded border border-neutral-200 text-xs dark:border-neutral-700">
      {SETTABLE.map((s) => (
        <button
          key={s.key}
          className={`px-2 py-0.5 ${
            value === s.key
              ? 'bg-neutral-800 text-white dark:bg-neutral-200 dark:text-neutral-900'
              : 'text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800'
          }`}
          onClick={() => onChange(s.key)}
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}

function candidateToHit(c: { title: string; author: string | null; isbn13: string | null }): BookHit {
  return { title: c.title, author: c.author, series: null, isbn13: c.isbn13, cover: null, year: null }
}

export function WishlistScreen({
  token,
  libraryFolderId,
  rows,
  viewerName,
  wantCandidates,
  listCandidates,
  seriesGapCandidates,
  onBack,
}: {
  token: string
  libraryFolderId: string
  rows: BookRow[]
  viewerName: string
  // Hardcover "want to read" books not in the library — wishlist candidates.
  wantCandidates: WantCandidate[]
  // Books on curated Hardcover lists this library part-owns (prompts/31 E2).
  listCandidates: ListCandidate[]
  // Books missing below the highest entry you own in a series (2026-09-11).
  seriesGapCandidates: ReleaseItem[]
  onBack: () => void
}) {
  const [list, setList] = useState<Wishlist>(EMPTY_WISHLIST)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showDeclined, setShowDeclined] = useState(false)
  const [showAcquired, setShowAcquired] = useState(false)
  const [showAllWant, setShowAllWant] = useState(false)
  const [showAllList, setShowAllList] = useState(false)
  const [showAllGaps, setShowAllGaps] = useState(false)

  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<BookHit[] | null>(null)
  const [searching, setSearching] = useState(false)

  const rowsRef = useRef(rows)
  rowsRef.current = rows

  // The token changes on every silent renewal — read it through a ref inside
  // the load effect so a renewal doesn't re-run the load and wipe local edits
  // that haven't reached Drive yet.
  const tokenRef = useRef(token)
  tokenRef.current = token

  // Track the Drive file id across rapid successive saves so two quick edits
  // don't each take the "no file yet" path and create duplicate files.
  const fileIdRef = useRef<string | null>(null)

  // Load + reconcile once (per library folder).
  useEffect(() => {
    let cancelled = false
    loadWishlist(tokenRef.current, libraryFolderId)
      .then(async (loaded) => {
        if (cancelled) return
        fileIdRef.current = loaded.fileId
        const { list: reconciled, changed } = reconcile(loaded, rowsRef.current)
        setList(reconciled)
        setLoading(false)
        if (changed) {
          try {
            const saved = await saveWishlist(token, libraryFolderId, reconciled)
            fileIdRef.current = saved.fileId
            if (!cancelled) setList(saved)
          } catch {
            /* keep the reconciled state in memory */
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError('Could not load the wishlist.')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [libraryFolderId])

  // Every wishlist edit rewrites the whole ~50 KB file. Clicking several
  // "Request" / status buttons in a row used to fire that many PATCHes back to
  // back — enough to trip Drive's per-user write limit (403). Update the UI
  // immediately, but coalesce the Drive writes: one save ~1s after the last
  // edit, always sending the newest state, retrying on failure.
  const latestRef = useRef<Wishlist>(list)
  latestRef.current = list
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const savingRef = useRef(false)

  async function flushSave() {
    saveTimerRef.current = null
    if (savingRef.current) {
      scheduleSave(50) // a save is in flight — check again right after it
      return
    }
    savingRef.current = true
    try {
      const saved = await saveWishlist(token, libraryFolderId, {
        ...latestRef.current,
        fileId: latestRef.current.fileId ?? fileIdRef.current,
      })
      fileIdRef.current = saved.fileId
      latestRef.current = { ...latestRef.current, fileId: saved.fileId }
      setError(null)
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err)
      setError(
        isAuthError(err)
          ? 'Sign-in expired — reload the page to sign in again, then retry.'
          : `Change saved locally but not to Drive (${detail}). Kept locally — retrying…`,
      )
      scheduleSave(6000)
    } finally {
      savingRef.current = false
    }
  }

  function scheduleSave(delay = 1000) {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => void flushSave(), delay)
  }

  function persist(next: Wishlist) {
    setList(next)
    latestRef.current = next
    scheduleSave()
  }

  // Don't lose a pending save when the screen closes.
  useEffect(
    () => () => {
      if (saveTimerRef.current) void flushSave()
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  async function runSearch() {
    if (!query.trim()) return
    setSearching(true)
    setError(null)
    setHits(null)
    try {
      setHits(await searchBooks(query))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed.')
    } finally {
      setSearching(false)
    }
  }

  function add(hit: BookHit) {
    void persist({ ...list, items: [hitToItem(hit, viewerName), ...list.items] })
    void logActivity(
      token,
      libraryFolderId,
      viewerName,
      'request',
      hit.author ? `${hit.title} — ${hit.author}` : hit.title,
    )
  }

  function setStatus(item: WishlistItem, status: WishlistStatus) {
    void persist({
      ...list,
      items: list.items.map((i) => (i.id === item.id ? withStatus(i, status, viewerName) : i)),
    })
  }

  function remove(id: string) {
    void persist({ ...list, items: list.items.filter((i) => i.id !== id) })
  }

  function patch(id: string, fields: Partial<WishlistItem>) {
    void persist({
      ...list,
      items: list.items.map((i) => (i.id === id ? { ...i, ...fields } : i)),
    })
  }

  const counts = {
    wanted: list.items.filter((i) => i.status === 'wanted').length,
    sourced: list.items.filter((i) => i.status === 'sourced').length,
    declined: list.items.filter((i) => i.status === 'declined').length,
    acquired: list.items.filter((i) => i.status === 'acquired').length,
  }
  const visible = list.items.filter(
    (i) =>
      i.status === 'wanted' ||
      i.status === 'sourced' ||
      (i.status === 'declined' && showDeclined) ||
      (i.status === 'acquired' && showAcquired),
  )

  // Hardcover want-to-read books we don't own and haven't listed yet.
  const wantSuggestions = wantCandidates.filter(
    (c) => !libraryMatch(c, rows) && !alreadyListed(candidateToHit(c), list.items),
  )
  const shownWant = showAllWant ? wantSuggestions : wantSuggestions.slice(0, 20)

  const listSuggestions = listCandidates.filter(
    (c) => !libraryMatch(c, rows) && !alreadyListed(candidateToHit(c), list.items),
  )
  const shownList = showAllList ? listSuggestions : listSuggestions.slice(0, 20)

  // Series gaps already exclude what you own (computeSeriesGaps works off the
  // current rows), so this only needs the wishlist-dedup check.
  const gapSuggestions = seriesGapCandidates.filter(
    (c) => !alreadyListed(candidateToHit(c), list.items),
  )
  const shownGaps = showAllGaps ? gapSuggestions : gapSuggestions.slice(0, 20)

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">Wishlist &amp; requests</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Books anyone wants added to the library. Search, add the right one, and it ticks itself off
        once a matching book turns up. Mark one <em>Sourced</em> when it's on the way, or{' '}
        <em>Declined</em> if it's not happening. Synced via a file in the Drive library folder.
      </p>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      <div className="card mt-4 p-4">
        <div className="flex gap-2">
          <input
            className="field min-w-0 flex-1"
            placeholder="Title, author, or both…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && runSearch()}
          />
          <button
            className="btn btn-primary shrink-0"
            disabled={!query.trim() || searching}
            onClick={runSearch}
          >
            {searching ? 'Searching…' : 'Search'}
          </button>
        </div>

        {hits && hits.length === 0 && (
          <p className="mt-3 text-sm text-neutral-400">No matches — try different words.</p>
        )}
        {hits && hits.length > 0 && (
          <ul className="mt-3 divide-y divide-neutral-100 dark:divide-neutral-800">
            {hits.map((hit, i) => {
              const inLibrary = libraryMatch(hit, rows)
              const listed = alreadyListed(hit, list.items)
              return (
                <li key={i} className="flex items-center gap-3 py-2">
                  <CoverThumb url={hit.cover} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{hit.title}</div>
                    <div className="truncate text-xs text-neutral-500">
                      {hit.author ?? 'Unknown author'}
                      {hit.year ? ` · ${hit.year}` : ''}
                    </div>
                  </div>
                  {inLibrary ? (
                    <span className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400">
                      In library
                    </span>
                  ) : listed ? (
                    <span className="badge bg-neutral-100 text-neutral-500 dark:bg-neutral-800">
                      On list
                    </span>
                  ) : (
                    <button className="btn btn-neutral btn-xs" onClick={() => add(hit)}>
                      Request
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {wantSuggestions.length > 0 && (
        <div className="card mt-4 p-4">
          <h2 className="text-sm font-medium">
            From your Hardcover want-to-read{' '}
            <span className="font-normal text-neutral-400">({wantSuggestions.length})</span>
          </h2>
          <p className="mt-1 text-xs text-neutral-500">
            Books you&rsquo;ve flagged &ldquo;want to read&rdquo; on Hardcover that aren&rsquo;t in
            the library yet.
          </p>
          <ul className="mt-3 divide-y divide-neutral-100 dark:divide-neutral-800">
            {shownWant.map((c, i) => (
              <li key={`${c.title}|${c.author ?? ''}|${i}`} className="flex items-center gap-3 py-2">
                <CoverThumb url={null} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{c.title}</div>
                  <div className="truncate text-xs text-neutral-500">
                    {c.author ?? 'Unknown author'}
                  </div>
                </div>
                <button
                  className="btn btn-neutral btn-xs"
                  onClick={() => add(candidateToHit(c))}
                >
                  Request
                </button>
              </li>
            ))}
          </ul>
          {wantSuggestions.length > shownWant.length && (
            <button
              className="mt-2 text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
              onClick={() => setShowAllWant(true)}
            >
              Show all {wantSuggestions.length}
            </button>
          )}
        </div>
      )}

      {listSuggestions.length > 0 && (
        <div className="card mt-4 p-4">
          <h2 className="text-sm font-medium">
            From lists you&rsquo;d like{' '}
            <span className="font-normal text-neutral-400">({listSuggestions.length})</span>
          </h2>
          <p className="mt-1 text-xs text-neutral-500">
            Books from curated Hardcover lists this library already owns a good chunk of.
          </p>
          <ul className="mt-3 divide-y divide-neutral-100 dark:divide-neutral-800">
            {shownList.map((c, i) => (
              <li
                key={`${c.title}|${c.author ?? ''}|${i}`}
                className="flex items-center gap-3 py-2"
              >
                <CoverThumb url={null} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{c.title}</div>
                  <div className="truncate text-xs text-neutral-500">
                    {c.author ?? 'Unknown author'}
                    <span className="text-neutral-400"> &middot; {c.fromList}</span>
                  </div>
                </div>
                <button className="btn btn-neutral btn-xs" onClick={() => add(candidateToHit(c))}>
                  Request
                </button>
              </li>
            ))}
          </ul>
          {listSuggestions.length > shownList.length && (
            <button
              className="mt-2 text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
              onClick={() => setShowAllList(true)}
            >
              Show all {listSuggestions.length}
            </button>
          )}
        </div>
      )}

      {gapSuggestions.length > 0 && (
        <div className="card mt-4 p-4">
          <h2 className="text-sm font-medium">
            Missing from your series{' '}
            <span className="font-normal text-neutral-400">({gapSuggestions.length})</span>
          </h2>
          <p className="mt-1 text-xs text-neutral-500">
            Books below the newest one you own in a series you're partway through.
          </p>
          <ul className="mt-3 divide-y divide-neutral-100 dark:divide-neutral-800">
            {shownGaps.map((c, i) => (
              <li
                key={`${c.title}|${c.author ?? ''}|${i}`}
                className="flex items-center gap-3 py-2"
              >
                <CoverThumb url={null} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{c.title}</div>
                  <div className="truncate text-xs text-neutral-500">
                    {c.author ?? 'Unknown author'}
                    {c.series && (
                      <span className="text-neutral-400">
                        {' '}
                        &middot; {c.series}
                        {c.seriesPosition != null ? ` #${c.seriesPosition}` : ''}
                      </span>
                    )}
                  </div>
                </div>
                <button className="btn btn-neutral btn-xs" onClick={() => add(candidateToHit(c))}>
                  Request
                </button>
              </li>
            ))}
          </ul>
          {gapSuggestions.length > shownGaps.length && (
            <button
              className="mt-2 text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
              onClick={() => setShowAllGaps(true)}
            >
              Show all {gapSuggestions.length}
            </button>
          )}
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">
          {counts.wanted} wanted
          {counts.sourced > 0 ? ` · ${counts.sourced} sourced` : ''}
        </h2>
        <div className="flex gap-3 text-xs text-neutral-500">
          {counts.declined > 0 && (
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={showDeclined}
                onChange={(e) => setShowDeclined(e.target.checked)}
              />
              Declined ({counts.declined})
            </label>
          )}
          {counts.acquired > 0 && (
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={showAcquired}
                onChange={(e) => setShowAcquired(e.target.checked)}
              />
              Acquired ({counts.acquired})
            </label>
          )}
        </div>
      </div>

      {loading && <p className="mt-4 text-sm text-neutral-400">Loading…</p>}

      <ul className="mt-2 divide-y divide-neutral-100 dark:divide-neutral-800">
        {visible.map((item) => {
          const done = item.status === 'acquired'
          return (
            <li key={item.id} className="flex items-start gap-3 py-3">
              <CoverThumb url={item.cover} faded={done} />
              <div className="min-w-0 flex-1">
                <div className={`text-sm font-medium ${done ? 'text-neutral-400 line-through' : ''}`}>
                  {item.title}
                </div>
                <div className="text-xs text-neutral-500">{item.author ?? 'Unknown author'}</div>
                <div className="mt-0.5 text-xs text-neutral-400">
                  {item.requestedBy ? `Requested by ${item.requestedBy}` : 'Added directly'}
                  {item.addedAt ? ` · ${whenText(item.addedAt)}` : ''}
                </div>

                {done ? (
                  <div className="mt-1 text-xs text-emerald-600 dark:text-emerald-400">
                    In the library
                    {item.acquiredAt ? ` · ${whenText(item.acquiredAt)}` : ''}
                  </div>
                ) : (
                  <>
                    <StatusPicker value={item.status} onChange={(s) => setStatus(item, s)} />
                    <input
                      className="mt-1 w-full border-0 border-b border-transparent bg-transparent p-0 text-xs text-neutral-500 focus:border-neutral-300 focus:outline-none dark:focus:border-neutral-600"
                      placeholder="Add a note (edition, why you want it…)"
                      defaultValue={item.note}
                      onBlur={(e) => {
                        if (e.target.value !== item.note) patch(item.id, { note: e.target.value })
                      }}
                    />
                    {(item.status === 'sourced' || item.status === 'declined') && (
                      <input
                        className="mt-1 w-full border-0 border-b border-transparent bg-transparent p-0 text-xs text-neutral-500 focus:border-neutral-300 focus:outline-none dark:focus:border-neutral-600"
                        placeholder={
                          item.status === 'sourced'
                            ? 'Where from / when it should arrive…'
                            : 'Why not? (out of print, too pricey…)'
                        }
                        defaultValue={item.statusNote}
                        onBlur={(e) => {
                          if (e.target.value !== item.statusNote)
                            patch(item.id, { statusNote: e.target.value })
                        }}
                      />
                    )}
                    {item.statusBy && (item.status === 'sourced' || item.status === 'declined') && (
                      <div className="mt-0.5 text-xs text-neutral-400">
                        {item.status === 'sourced' ? 'Sourced' : 'Declined'} by {item.statusBy}
                        {item.statusAt ? ` · ${whenText(item.statusAt)}` : ''}
                      </div>
                    )}
                  </>
                )}
              </div>
              <button
                className="shrink-0 text-xs text-neutral-400 underline hover:text-red-600"
                onClick={() => remove(item.id)}
              >
                Remove
              </button>
            </li>
          )
        })}
        {!loading && visible.length === 0 && (
          <li className="py-4 text-sm text-neutral-400">
            {list.items.length === 0 ? 'No requests yet.' : 'Nothing outstanding.'}
          </li>
        )}
      </ul>
    </div>
  )
}

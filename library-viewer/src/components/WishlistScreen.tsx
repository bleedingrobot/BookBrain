import { useEffect, useRef, useState } from 'react'
import type { BookRow } from '../lib/books'
import { searchGoogleBooks, type BookHit } from '../lib/googleBooks'
import {
  alreadyListed,
  EMPTY_WISHLIST,
  hitToItem,
  libraryMatch,
  loadWishlist,
  reconcile,
  saveWishlist,
  type Wishlist,
  type WishlistItem,
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

export function WishlistScreen({
  token,
  libraryFolderId,
  rows,
  onBack,
}: {
  token: string
  libraryFolderId: string
  rows: BookRow[]
  onBack: () => void
}) {
  const [list, setList] = useState<Wishlist>(EMPTY_WISHLIST)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAcquired, setShowAcquired] = useState(false)

  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<BookHit[] | null>(null)
  const [searching, setSearching] = useState(false)

  const rowsRef = useRef(rows)
  rowsRef.current = rows

  // Track the Drive file id across rapid successive saves so two quick edits
  // don't each take the "no file yet" path and create duplicate files.
  const fileIdRef = useRef<string | null>(null)

  // Load + reconcile once.
  useEffect(() => {
    let cancelled = false
    loadWishlist(token, libraryFolderId)
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
          setError('Could not load your wishlist.')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [token, libraryFolderId])

  async function persist(next: Wishlist) {
    setList(next)
    try {
      const saved = await saveWishlist(token, libraryFolderId, {
        ...next,
        fileId: fileIdRef.current,
      })
      fileIdRef.current = saved.fileId
      setList(saved)
    } catch {
      setError('Change saved locally but not to Drive — try again.')
    }
  }

  async function runSearch() {
    if (!query.trim()) return
    setSearching(true)
    setError(null)
    setHits(null)
    try {
      setHits(await searchGoogleBooks(query))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed.')
    } finally {
      setSearching(false)
    }
  }

  function add(hit: BookHit) {
    void persist({ ...list, items: [hitToItem(hit), ...list.items] })
  }

  function setAcquired(item: WishlistItem, acquired: boolean) {
    void persist({
      ...list,
      items: list.items.map((i) =>
        i.id === item.id
          ? { ...i, acquired, acquiredAt: acquired ? new Date().toISOString() : null }
          : i,
      ),
    })
  }

  function remove(id: string) {
    void persist({ ...list, items: list.items.filter((i) => i.id !== id) })
  }

  function setNote(id: string, note: string) {
    void persist({
      ...list,
      items: list.items.map((i) => (i.id === id ? { ...i, note } : i)),
    })
  }

  const visible = list.items.filter((i) => showAcquired || !i.acquired)
  const wantedCount = list.items.filter((i) => !i.acquired).length

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">Wishlist</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Books you want but don't own. Search, add the right one, and it ticks itself off once a
        matching book turns up in your library. Synced via a file in your Drive library folder.
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
                      Add
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="mt-6 flex items-center justify-between">
        <h2 className="text-sm font-medium">
          {wantedCount} to get
          {list.items.length > wantedCount ? ` · ${list.items.length - wantedCount} acquired` : ''}
        </h2>
        <label className="flex items-center gap-1.5 text-xs text-neutral-500">
          <input
            type="checkbox"
            checked={showAcquired}
            onChange={(e) => setShowAcquired(e.target.checked)}
          />
          Show acquired
        </label>
      </div>

      {loading && <p className="mt-4 text-sm text-neutral-400">Loading…</p>}

      <ul className="mt-2 divide-y divide-neutral-100 dark:divide-neutral-800">
        {visible.map((item) => (
          <li key={item.id} className="flex items-start gap-3 py-3">
            <input
              type="checkbox"
              className="mt-1 accent-emerald-600"
              checked={item.acquired}
              onChange={(e) => setAcquired(item, e.target.checked)}
            />
            <CoverThumb url={item.cover} faded={item.acquired} />
            <div className="min-w-0 flex-1">
              <div
                className={`text-sm font-medium ${item.acquired ? 'text-neutral-400 line-through' : ''}`}
              >
                {item.title}
              </div>
              <div className="text-xs text-neutral-500">{item.author ?? 'Unknown author'}</div>
              {item.acquired ? (
                <div className="text-xs text-emerald-600 dark:text-emerald-400">In your library</div>
              ) : (
                <input
                  className="mt-1 w-full border-0 border-b border-transparent bg-transparent p-0 text-xs text-neutral-500 focus:border-neutral-300 focus:outline-none dark:focus:border-neutral-600"
                  placeholder="Add a note (edition, why you want it…)"
                  defaultValue={item.note}
                  onBlur={(e) => {
                    if (e.target.value !== item.note) setNote(item.id, e.target.value)
                  }}
                />
              )}
            </div>
            <button
              className="shrink-0 text-xs text-neutral-400 underline hover:text-red-600"
              onClick={() => remove(item.id)}
            >
              Remove
            </button>
          </li>
        ))}
        {!loading && visible.length === 0 && (
          <li className="py-4 text-sm text-neutral-400">
            {wantedCount === 0 && list.items.length === 0
              ? 'Nothing on the wishlist yet.'
              : 'Nothing outstanding.'}
          </li>
        )}
      </ul>
    </div>
  )
}

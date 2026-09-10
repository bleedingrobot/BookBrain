import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../services/api'
import type { AcquireBook, AcquireSuggestion, OpenRequest, RequestCandidate } from '../types/acquire'

type RowState = { status: 'idle' | 'working' | 'done' | 'error'; message?: string }

const PREFERRED_FORMATS = ['epub', 'kepub', 'mobi', 'azw3', 'cbz', 'cbr']

function candidateLine(c: RequestCandidate): string {
  return [c.title || c.full, c.size, c.server].filter(Boolean).join(' · ')
}

function RequestRow({ req }: { req: OpenRequest }) {
  const queryClient = useQueryClient()
  const [showAlts, setShowAlts] = useState(false)
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['open-requests'] })

  const approve = useMutation({
    mutationFn: (full?: string) => api.approveOpenRequest(req.request_id, full),
    onSuccess: invalidate,
  })
  const skip = useMutation({
    mutationFn: () => api.skipOpenRequest(req.request_id),
    onSuccess: invalidate,
  })

  const err =
    approve.error instanceof ApiError ? approve.error.message : approve.isError ? 'Download failed' : null

  return (
    <li className="flex gap-3 py-3">
      <span className="flex h-16 w-11 shrink-0 items-center justify-center overflow-hidden rounded bg-neutral-200 text-neutral-400 dark:bg-neutral-800">
        {req.cover ? (
          <img
            src={req.cover}
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

      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{req.title}</div>
        <div className="text-xs text-neutral-500">
          {req.author || 'Unknown author'}
          {req.requested_by ? ` · asked by ${req.requested_by}` : ''}
        </div>

        {req.status === 'approved' && (
          <p className="mt-1 text-xs text-emerald-600">✓ Sourced — added to the Book Dump</p>
        )}

        {req.status === 'no_match' && (
          <div className="mt-1 flex items-center gap-3 text-xs text-neutral-500">
            <span>No EPUB found yet — try again after the next search.</span>
            <button className="underline hover:text-neutral-800" onClick={() => skip.mutate()}>
              skip
            </button>
          </div>
        )}

        {(req.status === 'pending' || req.status === 'failed') && req.candidate && (
          <div className="mt-1.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-neutral-600 dark:text-neutral-300">
                {candidateLine(req.candidate)}
              </span>
              <button
                className="rounded bg-neutral-900 px-2 py-0.5 text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
                disabled={approve.isPending}
                onClick={() => approve.mutate(undefined)}
              >
                {approve.isPending ? 'Getting…' : 'Get this'}
              </button>
              {req.alternatives.length > 0 && (
                <button
                  className="underline text-neutral-500 hover:text-neutral-800"
                  onClick={() => setShowAlts((v) => !v)}
                >
                  {showAlts ? 'hide' : `${req.alternatives.length} other${req.alternatives.length === 1 ? '' : 's'}`}
                </button>
              )}
              <button
                className="underline text-neutral-500 hover:text-neutral-800"
                onClick={() => skip.mutate()}
              >
                skip
              </button>
            </div>

            {showAlts && (
              <ul className="mt-1 space-y-1 border-l border-neutral-200 pl-3 dark:border-neutral-700">
                {req.alternatives.map((alt) => (
                  <li key={alt.full} className="flex items-center gap-2 text-xs">
                    <span className="text-neutral-500">{candidateLine(alt)}</span>
                    <button
                      className="rounded border border-neutral-300 px-2 py-0.5 disabled:opacity-50 dark:border-neutral-700"
                      disabled={approve.isPending}
                      onClick={() => approve.mutate(alt.full)}
                    >
                      Get
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {req.status === 'failed' && req.message && (
              <p className="mt-1 text-xs text-amber-600">Last attempt failed: {req.message}</p>
            )}
            {err && <p className="mt-1 text-xs text-red-600">{err}</p>}
          </div>
        )}
      </div>
    </li>
  )
}

function SuggestionList({
  title,
  hint,
  items,
  onFind,
}: {
  title: string
  hint: string
  items: AcquireSuggestion[]
  onFind: (query: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [showAll, setShowAll] = useState(false)
  if (items.length === 0) return null
  const shown = showAll ? items : items.slice(0, 40)
  return (
    <div className="mt-2">
      <button
        className="flex w-full items-center gap-2 text-left text-sm font-medium"
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
        {title} <span className="text-neutral-400">· {items.length}</span>
      </button>
      {open && (
        <>
          <p className="mt-0.5 pl-4 text-xs text-neutral-500">{hint}</p>
          <ul className="mt-1 divide-y divide-neutral-100 pl-4 dark:divide-neutral-800">
            {shown.map((s, i) => (
              <li key={`${s.title}-${i}`} className="flex items-center justify-between gap-3 py-1.5 text-sm">
                <span className="min-w-0">
                  {s.title}
                  <span className="text-neutral-500">
                    {s.author ? ` — ${s.author}` : ''}
                    {s.from_list ? ` · ${s.from_list}` : ''}
                  </span>
                </span>
                <button
                  className="shrink-0 rounded border border-neutral-300 px-2 py-0.5 text-xs dark:border-neutral-700"
                  onClick={() => onFind([s.title, s.author].filter(Boolean).join(' '))}
                >
                  Find
                </button>
              </li>
            ))}
          </ul>
          {items.length > 40 && !showAll && (
            <button
              className="mt-1 pl-4 text-xs text-neutral-400 underline"
              onClick={() => setShowAll(true)}
            >
              Show all {items.length}
            </button>
          )}
        </>
      )}
    </div>
  )
}

function Suggestions({ onFind }: { onFind: (query: string) => void }) {
  const q = useQuery({ queryKey: ['acquire-suggestions'], queryFn: api.acquireSuggestions })
  const want = q.data?.want_to_read ?? []
  const lists = q.data?.from_lists ?? []
  if (want.length === 0 && lists.length === 0) return null

  return (
    <div className="mt-4 rounded border border-neutral-200 p-3 dark:border-neutral-800">
      <h2 className="text-sm font-medium">Ideas to look for</h2>
      <p className="mt-1 text-xs text-neutral-500">
        Not on the Wishlist, but likely wanted — pulled from your Hardcover data. “Find” runs the
        search below.
      </p>
      <SuggestionList
        title="From your Hardcover want-to-read"
        hint="Books you marked “want to read” on Hardcover that aren’t in the library."
        items={want}
        onFind={onFind}
      />
      <SuggestionList
        title="From lists you’d like"
        hint="Books on curated Hardcover lists you already part-own."
        items={lists}
        onFind={onFind}
      />
    </div>
  )
}

function OpenRequests() {
  const queryClient = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)

  const requests = useQuery({ queryKey: ['open-requests'], queryFn: api.listOpenRequests })

  const job = useQuery({
    queryKey: ['open-requests-job', jobId],
    queryFn: () => api.openRequestsRefreshStatus(jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && q.state.data.status !== 'running' ? false : 2000),
  })

  useEffect(() => {
    if (jobId && job.data && job.data.status !== 'running') {
      setJobId(null)
      queryClient.invalidateQueries({ queryKey: ['open-requests'] })
    }
  }, [jobId, job.data, queryClient])

  const refresh = useMutation({
    mutationFn: api.refreshOpenRequests,
    onSuccess: (j) => setJobId(j.job_id),
  })

  const rows = requests.data ?? []
  const pending = rows.filter((r) => r.status === 'pending').length
  const searching = !!jobId || refresh.isPending

  return (
    <div className="mt-4 rounded border border-neutral-200 p-3 dark:border-neutral-800">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">
          Open requests from the library{' '}
          <span className="text-neutral-400">
            {pending > 0 ? `· ${pending} ready to get` : rows.length > 0 ? `· ${rows.length}` : ''}
          </span>
        </h2>
        <button
          className="rounded border border-neutral-300 px-2.5 py-1 text-xs disabled:opacity-50 dark:border-neutral-700"
          disabled={searching}
          onClick={() => refresh.mutate()}
        >
          {searching ? 'Searching…' : 'Search open requests'}
        </button>
      </div>

      <p className="mt-1 text-xs text-neutral-500">
        Everything on the household{' '}
        <Link to="/wishlist" className="underline">
          Wishlist
        </Link>{' '}
        still marked “wanted”. Searching checks OpenBooks for an EPUB of each (≈10s apiece). Getting
        one drops it in the Book Dump and marks the request “sourced”.
      </p>

      {searching && job.data && (
        <p className="mt-2 text-xs text-neutral-500">
          {job.data.searched}/{job.data.total || '…'} searched · {job.data.with_candidates} found
        </p>
      )}
      {refresh.error instanceof ApiError && (
        <p className="mt-2 text-xs text-red-600">{refresh.error.message}</p>
      )}

      {requests.isLoading ? (
        <p className="mt-3 text-xs text-neutral-400">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-3 text-xs text-neutral-400">
          No open requests. When someone marks a book “wanted” on the Wishlist, hit “Search open
          requests”.
        </p>
      ) : (
        <ul className="mt-1 divide-y divide-neutral-100 dark:divide-neutral-800">
          {rows.map((req) => (
            <RequestRow key={req.request_id} req={req} />
          ))}
        </ul>
      )}
    </div>
  )
}

function ServerControl() {
  const queryClient = useQueryClient()
  const server = useQuery({
    queryKey: ['openbooks-server'],
    queryFn: api.openBooksServerStatus,
    refetchInterval: (q) => (q.state.data?.running ? 15000 : 4000),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['openbooks-server'] })
  const start = useMutation({ mutationFn: api.startOpenBooksServer, onSuccess: refresh })
  const stop = useMutation({ mutationFn: api.stopOpenBooksServer, onSuccess: refresh })

  const running = server.data?.running ?? false
  const busy = start.isPending || stop.isPending
  const err =
    start.error instanceof ApiError
      ? start.error.message
      : stop.error instanceof ApiError
        ? stop.error.message
        : null

  return (
    <div className="mt-4 flex flex-wrap items-center gap-3 rounded border border-neutral-200 px-3 py-2 text-sm dark:border-neutral-800">
      <span className="flex items-center gap-2">
        <span
          className={`h-2 w-2 rounded-full ${running ? 'bg-emerald-500' : 'bg-neutral-400'}`}
        />
        OpenBooks server{' '}
        <span className="text-neutral-500">
          {running
            ? server.data?.managed
              ? '· running (started here)'
              : '· running'
            : '· stopped'}
        </span>
      </span>

      {running ? (
        <button
          className="rounded border border-neutral-300 px-2.5 py-1 text-xs disabled:opacity-50 dark:border-neutral-700"
          disabled={busy}
          onClick={() => stop.mutate()}
        >
          {stop.isPending ? 'Stopping…' : 'Stop'}
        </button>
      ) : (
        <button
          className="rounded bg-neutral-900 px-2.5 py-1 text-xs text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
          disabled={busy || server.data?.installed === false}
          onClick={() => start.mutate()}
        >
          {start.isPending ? 'Starting…' : 'Start'}
        </button>
      )}

      {server.data?.installed === false && (
        <span className="text-xs text-amber-600">
          openbooks.exe not found in backend/tools/ — download it from
          github.com/evan-buss/openbooks/releases
        </span>
      )}
      {err && <span className="text-xs text-red-600">{err}</span>}
    </div>
  )
}

export function Acquire() {
  const status = useQuery({ queryKey: ['acquire-status'], queryFn: api.acquireStatus })

  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [epubOnly, setEpubOnly] = useState(true)
  const [filter, setFilter] = useState('')
  const [limit, setLimit] = useState(60)
  const [rowStates, setRowStates] = useState<Record<string, RowState>>({})

  const search = useMutation({
    mutationFn: (q: string) => api.acquireSearch(q),
    onMutate: () => setRowStates({}),
  })

  const download = useMutation({
    mutationFn: (book: AcquireBook) => {
      const ext = book.format ? `.${book.format.toLowerCase()}` : ''
      const base = `${book.author} - ${book.title}`.replace(/\s+/g, ' ').trim()
      const filename = base.toLowerCase().endsWith(ext) ? base : `${base}${ext}`
      return api.acquireDownload(book.full, filename)
    },
    onMutate: (book) => setRow(book.full, { status: 'working' }),
    onSuccess: (res, book) =>
      setRow(book.full, { status: 'done', message: `Added ${res.filename} to the Book Dump` }),
    onError: (err, book) =>
      setRow(book.full, {
        status: 'error',
        message: err instanceof ApiError ? err.message : 'Download failed',
      }),
  })

  function setRow(full: string, state: RowState) {
    setRowStates((prev) => ({ ...prev, [full]: state }))
  }

  const results = useMemo(() => search.data?.results ?? [], [search.data])

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return results.filter((b) => {
      if (epubOnly && b.format.toLowerCase() !== 'epub') return false
      if (!needle) return true
      return `${b.author} ${b.title}`.toLowerCase().includes(needle)
    })
  }, [results, epubOnly, filter])

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const fa = PREFERRED_FORMATS.indexOf(a.format.toLowerCase())
      const fb = PREFERRED_FORMATS.indexOf(b.format.toLowerCase())
      return (fa === -1 ? 99 : fa) - (fb === -1 ? 99 : fb)
    })
  }, [filtered])

  function runSearch(explicit?: string) {
    const q = (explicit ?? query).trim()
    if (!q) return
    if (explicit !== undefined) setQuery(explicit)
    setSubmitted(q)
    setLimit(60)
    setFilter('')
    search.mutate(q)
  }

  if (status.isLoading) {
    return <div className="p-6 text-sm text-neutral-500">Loading…</div>
  }

  if (!status.data?.enabled) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <h1 className="text-xl font-semibold">Find a Book</h1>
        <p className="mt-3 text-sm text-neutral-500">
          The OpenBooks integration is turned off. To try it, set{' '}
          <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">OPENBOOKS_ENABLED=true</code>{' '}
          in <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">backend/.env</code> and
          restart the backend — then a Start button here launches the OpenBooks server for you.
        </p>
      </div>
    )
  }

  const searchError =
    search.error instanceof ApiError ? search.error.message : search.isError ? 'Search failed.' : null

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="text-xl font-semibold">Find a Book</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Experimental. Searches OpenBooks (IRC) for a title, downloads the one you pick, and drops it
        into the Book Dump — the normal scan &amp; identify pipeline takes it from there. Check the{' '}
        <Link to="/inbox" className="underline">
          Inbox
        </Link>{' '}
        after.
      </p>

      <ServerControl />
      <OpenRequests />
      <Suggestions
        onFind={(q) => {
          runSearch(q)
          document.getElementById('acquire-search')?.scrollIntoView({ behavior: 'smooth' })
        }}
      />

      <h2 id="acquire-search" className="mt-6 text-sm font-medium">
        Search for anything
      </h2>
      <div className="mt-2 flex gap-2">
        <input
          className="flex-1 rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          placeholder="title and author, e.g. mistborn brandon sanderson"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && runSearch()}
        />
        <button
          className="shrink-0 rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
          disabled={!query.trim() || search.isPending}
          onClick={() => runSearch()}
        >
          {search.isPending ? 'Searching…' : 'Search'}
        </button>
      </div>

      {search.isPending && (
        <p className="mt-3 text-sm text-neutral-500">
          Waiting for IRC results — this usually takes 10–30s.
        </p>
      )}
      {searchError && <p className="mt-3 text-sm text-red-600">{searchError}</p>}
      {search.data && search.data.message && (
        <p className="mt-3 text-sm text-neutral-500">{search.data.message} for “{submitted}”.</p>
      )}

      {results.length > 0 && (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-neutral-500">
            <span>
              {results.length} results for “{submitted}”
              {search.data && search.data.parse_errors > 0
                ? ` · ${search.data.parse_errors} unparseable`
                : ''}
            </span>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={epubOnly}
                onChange={(e) => setEpubOnly(e.target.checked)}
              />
              EPUB only
            </label>
            <input
              className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
              placeholder="filter results"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <span>{sorted.length} shown</span>
          </div>

          <table className="mt-3 w-full border-separate border-spacing-y-1 text-sm">
            <thead className="text-left text-xs text-neutral-400">
              <tr>
                <th className="px-2 py-1 font-normal">Title</th>
                <th className="px-2 py-1 font-normal">Author</th>
                <th className="px-2 py-1 font-normal">Format</th>
                <th className="px-2 py-1 font-normal">Size</th>
                <th className="px-2 py-1 font-normal">Server</th>
                <th className="px-2 py-1 font-normal"></th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, limit).map((book) => {
                const rs = rowStates[book.full] ?? { status: 'idle' as const }
                return (
                  <tr key={book.full} className="bg-neutral-50 dark:bg-neutral-900/50">
                    <td className="max-w-xs px-2 py-1.5">{book.title || '—'}</td>
                    <td className="px-2 py-1.5 text-neutral-500">{book.author || '—'}</td>
                    <td className="px-2 py-1.5 text-neutral-500">{book.format || '—'}</td>
                    <td className="px-2 py-1.5 text-neutral-500">{book.size || '—'}</td>
                    <td className="px-2 py-1.5 text-neutral-400">{book.server}</td>
                    <td className="px-2 py-1.5 text-right">
                      {rs.status === 'done' ? (
                        <span className="text-xs text-emerald-600">✓ {rs.message}</span>
                      ) : (
                        <button
                          className="rounded border border-neutral-300 px-2.5 py-1 text-xs disabled:opacity-50 dark:border-neutral-700"
                          disabled={rs.status === 'working' || download.isPending}
                          onClick={() => download.mutate(book)}
                        >
                          {rs.status === 'working' ? 'Getting…' : 'Get'}
                        </button>
                      )}
                      {rs.status === 'error' && (
                        <p className="mt-0.5 text-xs text-red-600">{rs.message}</p>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {sorted.length > limit && (
            <button
              className="mt-2 text-xs text-neutral-400 underline"
              onClick={() => setLimit((n) => n + 100)}
            >
              Show more ({sorted.length - limit} hidden)
            </button>
          )}
        </>
      )}
    </div>
  )
}

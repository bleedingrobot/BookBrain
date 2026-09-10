import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../services/api'
import type { AcquireBook } from '../types/acquire'

type RowState = { status: 'idle' | 'working' | 'done' | 'error'; message?: string }

const PREFERRED_FORMATS = ['epub', 'kepub', 'mobi', 'azw3', 'cbz', 'cbr']

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

  function runSearch() {
    const q = query.trim()
    if (!q) return
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

      <div className="mt-4 flex gap-2">
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
          onClick={runSearch}
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

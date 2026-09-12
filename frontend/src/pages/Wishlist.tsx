import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../services/api'
import type { ResolveResult } from '../types/wishlist'

function Cover({ url, className = '' }: { url: string | null; className?: string }) {
  return (
    <span
      className={`flex h-16 w-11 shrink-0 items-center justify-center overflow-hidden rounded bg-neutral-200 text-neutral-400 dark:bg-neutral-800 ${className}`}
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

export function Wishlist() {
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const [resolved, setResolved] = useState<ResolveResult | null>(null)
  const [showAll, setShowAll] = useState(false)

  const items = useQuery({ queryKey: ['wishlist'], queryFn: api.listWishlist })

  const resolve = useMutation({
    mutationFn: () => api.resolveWishlist(text),
    onSuccess: setResolved,
  })

  const add = useMutation({
    mutationFn: () => {
      const r = resolved!.resolved!
      return api.addWishlist({ ...r, raw_request: text })
    },
    onSuccess: () => {
      setText('')
      setResolved(null)
      queryClient.invalidateQueries({ queryKey: ['wishlist'] })
    },
  })

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'wanted' | 'acquired' }) =>
      api.setWishlistStatus(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['wishlist'] }),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteWishlist(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['wishlist'] }),
  })

  const visible = (items.data ?? []).filter((i) => showAll || i.status === 'wanted')
  const wantedCount = (items.data ?? []).filter((i) => i.status === 'wanted').length

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h1 className="text-xl font-semibold">Wishlist</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Books you want but don't own yet. Describe one and Claude will work out which book it is and
        check it against your library. Items tick themselves off once a matching book is imported.
      </p>

      <div className="mt-4 rounded border border-neutral-200 p-4 dark:border-neutral-800">
        <textarea
          className="w-full rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          rows={2}
          placeholder="e.g. the new brandon sanderson stormlight one — or — that grimdark trilogy about the blind assassin nun"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="mt-2 flex items-center gap-2">
          <button
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
            disabled={!text.trim() || resolve.isPending}
            onClick={() => {
              setResolved(null)
              resolve.mutate()
            }}
          >
            {resolve.isPending ? 'Looking up…' : 'Look up'}
          </button>
          {resolve.isError && (
            <span className="text-xs text-red-600">
              {resolve.error instanceof ApiError ? resolve.error.message : 'Lookup failed.'}
            </span>
          )}
        </div>

        {resolved && !resolved.found && (
          <p className="mt-3 text-sm text-neutral-500">
            Couldn't pin down a specific book.{resolved.note ? ` ${resolved.note}` : ''} Try adding
            the author, or a more exact title.
          </p>
        )}

        {resolved?.found && resolved.resolved && (
          <div className="mt-3 flex gap-3 rounded border border-neutral-200 p-3 dark:border-neutral-800">
            <Cover url={resolved.resolved.cover_url} />
            <div className="min-w-0 flex-1">
              <div className="font-medium">{resolved.resolved.title}</div>
              <div className="text-sm text-neutral-500">
                {resolved.resolved.author ?? 'Unknown author'}
                {resolved.resolved.series
                  ? ` · ${resolved.resolved.series}${
                      resolved.resolved.series_number ? ` #${resolved.resolved.series_number}` : ''
                    }`
                  : ''}
                {resolved.resolved.isbn13 ? ` · ${resolved.resolved.isbn13}` : ''}
              </div>
              {resolved.note && (
                <p className="mt-1 text-xs text-neutral-400">{resolved.note}</p>
              )}

              {resolved.already_in_library ? (
                <p className="mt-2 rounded bg-emerald-100 px-2 py-1 text-xs text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
                  You already have this —{' '}
                  <Link to="/library" className="underline">
                    {resolved.already_in_library.filename}
                  </Link>
                </p>
              ) : resolved.already_on_wishlist ? (
                <p className="mt-2 rounded bg-amber-100 px-2 py-1 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                  Already on your wishlist.
                </p>
              ) : (
                <button
                  className="mt-2 rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
                  disabled={add.isPending}
                  onClick={() => add.mutate()}
                >
                  {add.isPending ? 'Adding…' : 'Add to wishlist'}
                </button>
              )}
              {add.isError && (
                <p className="mt-1 text-xs text-red-600">
                  {add.error instanceof ApiError ? add.error.message : 'Failed to add.'}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="mt-6 flex items-center justify-between">
        <h2 className="text-sm font-medium">
          {wantedCount} to get
          {items.data && items.data.length > wantedCount
            ? ` · ${items.data.length - wantedCount} acquired`
            : ''}
        </h2>
        <label className="flex items-center gap-1.5 text-xs text-neutral-500">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
          Show acquired
        </label>
      </div>

      {items.isLoading && <p className="mt-4 text-sm text-neutral-500">Loading…</p>}

      <ul className="mt-2 divide-y divide-neutral-100 dark:divide-neutral-800">
        {visible.map((item) => (
          <li key={item.id} className="flex items-start gap-3 py-3">
            <input
              type="checkbox"
              className="mt-1 accent-emerald-600"
              checked={item.status === 'acquired'}
              onChange={(e) =>
                setStatus.mutate({ id: item.id, status: e.target.checked ? 'acquired' : 'wanted' })
              }
            />
            <Cover url={item.cover_url} className={item.status === 'acquired' ? 'opacity-50' : ''} />
            <div className="min-w-0 flex-1">
              <div
                className={`font-medium ${item.status === 'acquired' ? 'text-neutral-400 line-through' : ''}`}
              >
                {item.title}
              </div>
              <div className="text-sm text-neutral-500">
                {item.author ?? 'Unknown author'}
                {item.series
                  ? ` · ${item.series}${item.series_number ? ` #${item.series_number}` : ''}`
                  : ''}
              </div>
              {item.status === 'acquired' && item.acquired_file_id && (
                <p className="text-xs text-emerald-600 dark:text-emerald-400">
                  In your library ·{' '}
                  <Link to="/library" className="underline">
                    view
                  </Link>
                </p>
              )}
              <p className="mt-0.5 truncate text-xs text-neutral-400">“{item.raw_request}”</p>
            </div>
            <button
              className="shrink-0 text-xs text-neutral-400 underline hover:text-red-600"
              onClick={() => remove.mutate(item.id)}
            >
              Remove
            </button>
          </li>
        ))}
        {items.data && visible.length === 0 && (
          <li className="py-4 text-sm text-neutral-400">
            {wantedCount === 0 ? 'Nothing on the wishlist.' : 'Nothing outstanding — tick "Show acquired".'}
          </li>
        )}
      </ul>
    </div>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { loadActivityLog, type ActivityEvent } from '../lib/activityLog'

const TYPE_LABEL: Record<ActivityEvent['type'], string> = {
  'sign-in': 'Signed in',
  search: 'Searched',
  download: 'Downloaded',
  'kobo-send': 'Sent to Kobo',
}

export function ActivityScreen({
  token,
  libraryFolderId,
  onBack,
}: {
  token: string
  libraryFolderId: string
  onBack: () => void
}) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [who, setWho] = useState('all')

  function load() {
    setError(null)
    loadActivityLog(token, libraryFolderId)
      .then(setEvents)
      .catch(() => setError('Could not load the activity log.'))
  }

  useEffect(load, [token, libraryFolderId])

  const people = useMemo(
    () => Array.from(new Set((events ?? []).map((e) => e.who))).sort(),
    [events],
  )

  const visible = useMemo(() => {
    const list = events ?? []
    const filtered = who === 'all' ? list : list.filter((e) => e.who === who)
    return [...filtered].sort((a, b) => b.at.localeCompare(a.at))
  }, [events, who])

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <div className="mt-3 flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Activity</h1>
        <button className="btn btn-ghost btn-xs" onClick={load}>
          Refresh
        </button>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Sign-ins, searches, downloads, and Kobo sends. Stored as a file in your Drive library
        folder — visible to anyone with access to that folder.
      </p>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {people.length > 1 && (
        <div className="mt-4 flex flex-wrap gap-1.5">
          {['all', ...people].map((p) => (
            <button
              key={p}
              onClick={() => setWho(p)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                who === p
                  ? 'border-brand-600 bg-brand-600 text-white'
                  : 'border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800'
              }`}
            >
              {p === 'all' ? 'Everyone' : p}
            </button>
          ))}
        </div>
      )}

      {events === null && !error && <p className="mt-6 text-sm text-neutral-400">Loading…</p>}

      <ul className="mt-4 divide-y divide-neutral-100 dark:divide-neutral-800">
        {visible.map((e) => (
          <li key={e.id} className="py-2.5 text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-medium">
                {e.who} · {TYPE_LABEL[e.type]}
              </span>
              <span className="shrink-0 text-xs text-neutral-400">
                {new Date(e.at).toLocaleString()}
              </span>
            </div>
            {e.detail && <div className="mt-0.5 truncate text-neutral-500">{e.detail}</div>}
          </li>
        ))}
        {events !== null && visible.length === 0 && (
          <li className="py-4 text-sm text-neutral-400">Nothing logged yet.</li>
        )}
      </ul>
    </div>
  )
}

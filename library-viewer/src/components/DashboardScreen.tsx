import { useMemo } from 'react'
import type { BookRow } from '../lib/books'
import { SORTS, topGenres } from '../lib/books'
import { Cover } from './Cover'
import type { Dashboard } from '../lib/dashboard'
import type { LibraryIndex } from '../lib/libraryIndex'
import { timeAgo } from '../lib/news'
import {
  addedSince,
  avgPages,
  distinctCount,
  growthHistogram,
  newlyCompletedSeries,
  topOwnedSeries,
} from '../lib/libraryStats'
import { computeSeriesGaps } from '../lib/seriesGaps'

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-neutral-200 px-3 py-2.5 dark:border-neutral-800">
      <div className="text-lg font-semibold tracking-tight">{value}</div>
      <div className="text-xs text-neutral-400">{label}</div>
    </div>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-2 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
      {children}
    </h2>
  )
}

// The stacked "what's happening to the wishlist" bar: wanted (grey, not
// searched yet) → pending (amber, found, waiting on a download) → downloaded
// (brand, done). Widths are proportional to the whole queue, not just what's
// left, so the bar visibly fills in as the backlog clears.
function QueueBar({ queue }: { queue: Dashboard['queue'] }) {
  const total = queue.wanted + queue.pending + queue.approvedAllTime + queue.noMatch
  if (total === 0) return null
  const pct = (n: number) => (n / total) * 100
  return (
    <div className="flex h-3 overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
      <div className="h-full bg-brand-500" style={{ width: `${pct(queue.approvedAllTime)}%` }} />
      <div className="h-full bg-amber-400" style={{ width: `${pct(queue.pending)}%` }} />
      <div className="h-full bg-neutral-400 dark:bg-neutral-600" style={{ width: `${pct(queue.wanted)}%` }} />
      <div className="h-full bg-neutral-200 dark:bg-neutral-700" style={{ width: `${pct(queue.noMatch)}%` }} />
    </div>
  )
}

function GrowthSparkline({ rows }: { rows: BookRow[] }) {
  const days = useMemo(() => growthHistogram(rows, 14), [rows])
  const max = Math.max(1, ...days.map((d) => d.count))
  return (
    <div className="flex h-16 items-end gap-1">
      {days.map((d) => (
        <div key={d.date} className="group relative flex-1">
          <div
            className="w-full rounded-t bg-brand-500/70 transition-colors group-hover:bg-brand-500"
            style={{ height: `${Math.max(3, (d.count / max) * 100)}%` }}
          />
          <div className="pointer-events-none absolute bottom-full left-1/2 mb-1 hidden -translate-x-1/2 rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] whitespace-nowrap text-white group-hover:block dark:bg-neutral-700">
            {d.count} on {new Date(d.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          </div>
        </div>
      ))}
    </div>
  )
}

export function DashboardScreen({
  rows,
  index,
  dashboard,
  token,
  onBack,
  onOpenBook,
}: {
  rows: BookRow[]
  index: LibraryIndex
  dashboard: Dashboard
  token: string
  onBack: () => void
  onOpenBook: (id: string) => void
}) {
  const recent = useMemo(() => [...rows].sort(SORTS.added).slice(0, 5), [rows])
  const gaps = useMemo(() => computeSeriesGaps(rows, index.series), [rows, index.series])
  const completedSeries = useMemo(() => newlyCompletedSeries(rows, gaps, 3), [rows, gaps])
  const genre = topGenres(rows, 1)[0]
  const longestSeries = topOwnedSeries(rows, 1)[0]
  const pages = avgPages(rows)
  const readingNow = rows.filter((r) => r.reading?.status === 'reading').length
  const hasQueue = dashboard.generatedAt !== null
  const queueSize = dashboard.queue.wanted + dashboard.queue.pending

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">Dashboard</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        The shelf, the queue, and everything in between.
      </p>

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Stat label="books on the shelf" value={rows.length.toLocaleString()} />
        <Stat label="authors" value={distinctCount(rows, 'author').toLocaleString()} />
        <Stat label="series" value={distinctCount(rows, 'series').toLocaleString()} />
        <Stat label="added, last 24h" value={addedSince(rows, 24)} />
        <Stat label="added, last 7d" value={addedSince(rows, 24 * 7)} />
        <Stat label="in the queue" value={hasQueue ? queueSize.toLocaleString() : '—'} />
      </div>

      {/* --- library growth ------------------------------------------- */}
      <section className="mt-6">
        <SectionHeading>Growth, last 14 days</SectionHeading>
        <GrowthSparkline rows={rows} />
      </section>

      {/* --- recently added --------------------------------------------- */}
      {recent.length > 0 && (
        <section className="mt-6">
          <SectionHeading>Newest arrivals</SectionHeading>
          <ul className="space-y-2">
            {recent.map((r) => (
              <li key={r.id}>
                <button
                  className="flex w-full items-center gap-3 rounded-lg px-1 py-1 text-left hover:bg-neutral-50 dark:hover:bg-neutral-900"
                  onClick={() => onOpenBook(r.id)}
                >
                  <Cover token={token} driveId={r.id} isbn={r.isbn} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{r.title}</div>
                    <div className="truncate text-xs text-neutral-400">{r.author ?? 'Unknown author'}</div>
                  </div>
                  <div className="shrink-0 text-xs text-neutral-400">
                    {r.addedAt ? timeAgo(r.addedAt) : ''}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* --- recently completed series ----------------------------------- */}
      {completedSeries.length > 0 && (
        <section className="mt-6">
          <SectionHeading>Recently completed series</SectionHeading>
          <ul className="space-y-2">
            {completedSeries.map((s) => (
              <li key={s.name}>
                <button
                  className="flex w-full items-center gap-3 rounded-lg px-1 py-1 text-left hover:bg-neutral-50 dark:hover:bg-neutral-900"
                  onClick={() => s.latestBookId && onOpenBook(s.latestBookId)}
                >
                  <Cover token={token} driveId={s.latestBookId ?? ''} isbn={s.latestBookIsbn} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{s.name} 🎉</div>
                    <div className="truncate text-xs text-neutral-400">
                      {s.author ?? 'Unknown author'} · {s.bookCount} books, all owned
                    </div>
                  </div>
                  <div className="shrink-0 text-xs text-neutral-400">
                    {s.completedAt ? timeAgo(s.completedAt) : ''}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* --- fun facts ---------------------------------------------------- */}
      <section className="mt-6">
        <SectionHeading>Fun facts</SectionHeading>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {genre && <Stat label="top genre" value={genre} />}
          {longestSeries && <Stat label="longest series owned" value={`${longestSeries.name} (${longestSeries.count})`} />}
          {pages != null && <Stat label="avg. book length" value={`${pages.toLocaleString()} pg`} />}
          {readingNow > 0 && <Stat label="being read right now" value={readingNow} />}
        </div>
      </section>

      {/* --- acquisition pipeline ------------------------------------------ */}
      {hasQueue && (
        <section className="mt-6">
          <SectionHeading>The queue — what OpenBooks is hunting down</SectionHeading>
          <QueueBar queue={dashboard.queue} />
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-neutral-400">
            <span>
              <span className="inline-block h-2 w-2 rounded-full bg-brand-500 align-[-1px]" /> downloaded (
              {dashboard.queue.approvedAllTime})
            </span>
            <span>
              <span className="inline-block h-2 w-2 rounded-full bg-amber-400 align-[-1px]" /> found, queued (
              {dashboard.queue.pending})
            </span>
            <span>
              <span className="inline-block h-2 w-2 rounded-full bg-neutral-400 align-[-1px] dark:bg-neutral-600" />{' '}
              not searched yet ({dashboard.queue.wanted})
            </span>
            <span>
              <span className="inline-block h-2 w-2 rounded-full bg-neutral-200 align-[-1px] dark:bg-neutral-700" />{' '}
              no match found ({dashboard.queue.noMatch})
            </span>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="downloaded, 24h" value={dashboard.downloadsLast24h} />
            <Stat label="downloaded, 7d" value={dashboard.downloadsLast7d} />
            <Stat
              label="hit rate"
              value={dashboard.hitRate.pct != null ? `${dashboard.hitRate.pct}%` : '—'}
            />
            <Stat
              label="est. time left"
              value={dashboard.etaDays != null ? `~${Math.round(dashboard.etaDays)}d` : '—'}
            />
          </div>

          {dashboard.upNext.length > 0 && (
            <div className="mt-4">
              <h3 className="mb-1.5 text-[11px] font-semibold tracking-wide text-neutral-400 uppercase">
                Up next
              </h3>
              <ul className="space-y-1 text-sm">
                {dashboard.upNext.map((p, i) => (
                  <li key={i} className="flex items-center justify-between gap-2">
                    <span className="truncate">
                      {p.title}
                      {p.author && <span className="text-neutral-400"> — {p.author}</span>}
                    </span>
                    <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
                      {p.score != null ? `${Math.round(p.score * 100)}%` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {dashboard.recentDownloads.length > 0 && (
            <div className="mt-4">
              <h3 className="mb-1.5 text-[11px] font-semibold tracking-wide text-neutral-400 uppercase">
                Just fetched
              </h3>
              <ul className="space-y-1 text-sm">
                {dashboard.recentDownloads.map((d, i) => (
                  <li key={i} className="flex items-center justify-between gap-2">
                    <span className="truncate">
                      {d.title}
                      {d.author && <span className="text-neutral-400"> — {d.author}</span>}
                    </span>
                    <span className="shrink-0 text-xs text-neutral-400">{timeAgo(d.at)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-4 text-xs text-neutral-400">
            Snapshot from {timeAgo(dashboard.generatedAt)} — refreshes nightly and after every
            auto-get download.
          </p>
        </section>
      )}
    </div>
  )
}

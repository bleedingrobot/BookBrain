import { useMemo } from 'react'
import type { BookRow } from '../lib/books'
import { goalPace, type Reading, type ReadingGoal } from '../lib/reading'
import { readingStats } from '../lib/readingStats'

function paceLabel(goal: ReadingGoal): string {
  if (goal.progress >= goal.target) return 'goal reached 🎉'
  const ahead = goalPace(goal)
  if (ahead >= 1) return `${ahead} ahead of pace`
  if (ahead <= -1) return `${-ahead} behind pace`
  return 'on track'
}

function GoalBarInner({ goal }: { goal: ReadingGoal }) {
  const pct = Math.min(100, Math.round((goal.progress / goal.target) * 100))
  return (
    <>
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium text-neutral-600 dark:text-neutral-300">
          {goal.year} reading goal
        </span>
        <span className="text-neutral-400">
          {goal.progress} / {goal.target} · {paceLabel(goal)}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
        <div className="h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
      </div>
    </>
  )
}

// A slim goal line for the home screen. Clickable → the full stats screen.
export function ReadingGoalBar({ goal, onOpen }: { goal: ReadingGoal; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="mb-4 block w-full rounded-lg border border-neutral-200 px-3 py-2 text-left transition-colors hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-900"
    >
      <GoalBarInner goal={goal} />
    </button>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-neutral-200 px-3 py-2.5 dark:border-neutral-800">
      <div className="text-lg font-semibold tracking-tight">{value}</div>
      <div className="text-xs text-neutral-400">{label}</div>
    </div>
  )
}

export function ReadingStatsScreen({
  reading,
  rows,
  onBack,
}: {
  reading: Reading
  rows: BookRow[]
  onBack: () => void
}) {
  const stats = useMemo(() => readingStats(reading, rows), [reading, rows])
  const year = new Date().getUTCFullYear()
  const maxRating = Math.max(1, ...stats.ratingCounts.map((r) => r.count))

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">
        {reading.reader ? `${reading.reader}'s reading` : 'Reading stats'}
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Counts books that are both in this library and on the Hardcover shelves — books read but
        not owned aren&rsquo;t included.
      </p>

      {reading.goal && (
        <div className="mt-4 rounded-lg border border-neutral-200 px-3 py-2 dark:border-neutral-800">
          <GoalBarInner goal={reading.goal} />
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Stat label={`read in ${year}`} value={stats.readThisYear} />
        <Stat label="read all-time" value={stats.readAllTime} />
        <Stat label={`pages in ${year}`} value={stats.pagesThisYear.toLocaleString()} />
        <Stat label="this month" value={stats.thisMonth} />
        <Stat label="last month" value={stats.lastMonth} />
        <Stat label="avg rating you give" value={stats.avgRating ?? '—'} />
      </div>

      {stats.longestRead && (
        <p className="mt-3 text-sm text-neutral-500">
          Longest this year: <span className="font-medium">{stats.longestRead.title}</span> (
          {stats.longestRead.pages.toLocaleString()} pages)
        </p>
      )}

      {stats.ratingCounts.some((r) => r.count > 0) && (
        <section className="mt-6">
          <h2 className="mb-2 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
            Your ratings
          </h2>
          <div className="space-y-1">
            {[...stats.ratingCounts].reverse().map(({ rating, count }) => (
              <div key={rating} className="flex items-center gap-2 text-xs">
                <span className="w-8 shrink-0 text-right text-neutral-400">{rating}★</span>
                <div className="h-3 flex-1 overflow-hidden rounded bg-neutral-100 dark:bg-neutral-800">
                  <div
                    className="h-full rounded bg-amber-400"
                    style={{ width: `${(count / maxRating) * 100}%` }}
                  />
                </div>
                <span className="w-6 shrink-0 text-neutral-400">{count}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        {stats.topAuthors.length > 0 && (
          <section>
            <h2 className="mb-2 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
              Most-read authors
            </h2>
            <ul className="space-y-1 text-sm">
              {stats.topAuthors.map((a) => (
                <li key={a.name} className="flex justify-between gap-2">
                  <span className="truncate">{a.name}</span>
                  <span className="shrink-0 text-neutral-400">{a.count}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {stats.topSeries.length > 0 && (
          <section>
            <h2 className="mb-2 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
              Most-read series
            </h2>
            <ul className="space-y-1 text-sm">
              {stats.topSeries.map((s) => (
                <li key={s.name} className="flex justify-between gap-2">
                  <span className="truncate">{s.name}</span>
                  <span className="shrink-0 text-neutral-400">{s.count}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}

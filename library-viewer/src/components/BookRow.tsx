import { useState } from 'react'
import { sendKey, type BookRow as Row, type SendStatus } from '../lib/books'
import type { DriveFile } from '../lib/drive'
import type { RecBook } from '../lib/recommendations'
import type { ReadingStatus } from '../lib/reading'
import { monthYear } from '../lib/releases'
import type { SeriesGap } from '../lib/seriesGaps'
import type { KoboDevice } from '../lib/settings'
import { libraryMatch } from '../lib/wishlist'
import { Cover } from './Cover'

export type RequestResult = 'added' | 'already-listed' | 'owned'

interface Props {
  row: Row
  allRows: Row[]
  token: string
  // prompts/29 — semantic-search similarity (0..1), shown as a "· NN% match".
  matchScore?: number
  // prompts/30 — the reader name to attribute the reading badge to.
  reader?: string
  gap: SeriesGap | undefined
  recs: RecBook[] | undefined
  // prompts/31 Part B — mean rating per author, re-ranks the recs list.
  recAffinity?: Map<string, number>
  selected: boolean
  expanded: boolean
  sentDevices: KoboDevice[]
  koboDevices: KoboDevice[]
  sendState: Record<string, SendStatus>
  onToggleSelect: (id: string) => void
  onExpand: (id: string) => void
  onSelectMany: (ids: string[], on: boolean) => void
  onSend: (file: DriveFile, device: KoboDevice) => void
  onDownload: (file: DriveFile) => void
  onRead: (row: Row) => void
  // prompts/30 Phase 3 — queue a reading-status change for Hardcover write-back.
  onMarkRead?: (row: Row, status: ReadingStatus) => void
  onFilterAuthor: (author: string) => void
  onFilterSeries: (series: string) => void
  onFilterGenre: (genre: string) => void
  onFilterMood?: (mood: string) => void
  onRequestBook: (rec: RecBook) => Promise<RequestResult>
}

const READING_STATUS_LABELS: Record<ReadingStatus, string> = {
  read: 'Read',
  reading: 'Reading',
  want: 'Want to read',
  dnf: 'DNF',
}

// A star rating like "★ 4.4" — Hardcover's community average.
function RatingPill({ rating }: { rating: number }) {
  return (
    <span className="badge bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400">
      ★ {rating.toFixed(1)}
    </span>
  )
}

// prompts/30 — the owner's Hardcover reading status for this book.
function ReadingBadge({ reading, reader }: { reading: Row['reading']; reader: string }) {
  if (!reading?.status) return null
  const label = { read: '✓ Read', reading: 'Reading', want: 'Want to read', dnf: 'DNF' }[
    reading.status
  ]
  const tone =
    reading.status === 'read'
      ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400'
      : 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400'
  const when = reading.readDate ? ` · ${reading.readDate.slice(0, 4)}` : ''
  const title = `${reader || 'Reader'}${
    reading.status === 'read' ? `${reading.readDate ? ` — read ${reading.readDate}` : ' — read'}` : ` — ${label}`
  }${reading.rating ? ` · rated ${reading.rating}` : ''}`
  return (
    <span className={`badge ${tone}`} title={title}>
      {label}
      {reading.status === 'read' && when}
      {reading.rating ? ` ★${reading.rating}` : ''}
    </span>
  )
}

function ReadersAlsoLiked({
  recs,
  allRows,
  token,
  affinity,
  onRequestBook,
}: {
  recs: RecBook[]
  allRows: Row[]
  token: string
  // prompts/31 Part B — your mean rating per author. Recs by an author you
  // rate highly float up; ones you rate low (or DNF'd) sink. Unknown = neutral.
  affinity?: Map<string, number>
  onRequestBook: (rec: RecBook) => Promise<RequestResult>
}) {
  const [state, setState] = useState<Record<string, 'pending' | RequestResult>>({})

  const ordered =
    affinity && affinity.size > 0
      ? recs
          .map((rec, i) => ({ rec, i, score: affinity.get(rec.author ?? '') ?? 3.5 }))
          .sort((a, b) => b.score - a.score || a.i - b.i)
          .map((x) => x.rec)
      : recs

  async function request(rec: RecBook, key: string) {
    setState((s) => ({ ...s, [key]: 'pending' }))
    try {
      const result = await onRequestBook(rec)
      setState((s) => ({ ...s, [key]: result }))
    } catch {
      setState((s) => {
        const next = { ...s }
        delete next[key]
        return next
      })
    }
  }

  return (
    <div className="mt-3">
      <div className="text-xs font-medium text-neutral-500">Readers also liked</div>
      <ul className="mt-1.5 divide-y divide-neutral-100 dark:divide-neutral-800/60">
        {ordered.map((rec, i) => {
          const key = `${rec.title}|${rec.author ?? ''}`
          const owned = libraryMatch(rec, allRows) != null
          const st = owned ? 'owned' : state[key]
          return (
            <li key={i} className="flex items-center gap-2.5 py-1.5">
              <Cover token={token} driveId={`rec-${key}`} isbn={rec.isbn13} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-neutral-700 dark:text-neutral-300">
                  {rec.title}
                </div>
                <div className="truncate text-xs text-neutral-500">
                  {rec.author ?? 'Unknown author'}
                </div>
              </div>
              {st === 'owned' ? (
                <span className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400">
                  In library
                </span>
              ) : st === 'added' || st === 'already-listed' ? (
                <span className="badge bg-neutral-100 text-neutral-500 dark:bg-neutral-800">
                  On wishlist
                </span>
              ) : (
                <button
                  className="btn btn-neutral btn-xs"
                  disabled={st === 'pending'}
                  onClick={() => request(rec, key)}
                >
                  {st === 'pending' ? '…' : 'Request'}
                </button>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

const isEpub = (name: string) => name.toLowerCase().endsWith('.epub')

export function BookRow({
  row,
  allRows,
  token,
  matchScore,
  reader,
  gap,
  recs,
  recAffinity,
  selected,
  expanded,
  sentDevices,
  koboDevices,
  sendState,
  onToggleSelect,
  onExpand,
  onSelectMany,
  onSend,
  onDownload,
  onRead,
  onMarkRead,
  onFilterAuthor,
  onFilterSeries,
  onFilterGenre,
  onFilterMood,
  onRequestBook,
}: Props) {
  const seriesPeers = expanded && row.series ? allRows.filter((r) => r.series === row.series) : []
  const authorPeers = expanded && row.author ? allRows.filter((r) => r.author === row.author) : []
  const meta = row.meta
  // "Book" is the default category — only worth a badge when it's something else.
  const category = meta?.category && meta.category !== 'Book' ? meta.category : null

  return (
    <>
      <li
        id={`book-${row.id}`}
        className={`flex flex-wrap items-start gap-x-3 gap-y-2 py-2.5 ${
          expanded ? '' : 'border-b border-neutral-100 dark:border-neutral-800/60'
        }`}
      >
        <input
          type="checkbox"
          className="mt-1 accent-brand-600"
          checked={selected}
          onChange={() => onToggleSelect(row.id)}
        />
        <button
          type="button"
          className="mt-0.5 shrink-0"
          onClick={() => onExpand(row.id)}
          aria-label="Details"
        >
          <Cover token={token} driveId={row.id} isbn={row.isbn} />
        </button>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            className="block max-w-full truncate text-left font-medium text-neutral-900 dark:text-neutral-100"
            onClick={() => onExpand(row.id)}
          >
            {row.title}
          </button>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            {row.author && (
              <button
                type="button"
                className="text-xs text-neutral-500 hover:text-brand-600 hover:underline dark:hover:text-brand-400"
                onClick={() => onFilterAuthor(row.author!)}
              >
                {row.author}
              </button>
            )}
            {row.series && (
              <button
                type="button"
                className="badge bg-brand-50 text-brand-700 hover:bg-brand-100 dark:bg-brand-950/60 dark:text-brand-300 dark:hover:bg-brand-950"
                onClick={() => onFilterSeries(row.series!)}
              >
                {row.series}
                {row.seriesNumber && ` #${row.seriesNumber}`}
              </button>
            )}
            {matchScore != null && (
              <span className="text-xs text-neutral-400">
                · {Math.round(matchScore * 100)}% match
              </span>
            )}
            <ReadingBadge reading={row.reading} reader={reader ?? ''} />
            {meta?.rating != null && <RatingPill rating={meta.rating} />}
            {category && (
              <span className="badge bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                {category}
              </span>
            )}
            {sentDevices.map((d) => (
              <span
                key={d.folderId}
                className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
              >
                ✓ {d.label}
              </span>
            ))}
          </div>
        </div>
        <div className="flex w-full shrink-0 flex-wrap items-center gap-1 pl-7 sm:w-auto sm:pl-0">
          {isEpub(row.filename) && (
            <button className="btn btn-primary btn-xs" onClick={() => onRead(row)}>
              Read
            </button>
          )}
          <button className="btn btn-ghost btn-xs" onClick={() => onDownload(row.file)}>
            Download
          </button>
          {koboDevices.map((device) => {
            const status = sendState[sendKey(row.file.id, device.folderId)]
            const label =
              status === 'pending'
                ? 'Sending…'
                : status === 'error'
                  ? 'Failed — retry'
                  : koboDevices.length === 1
                    ? 'Send to Kobo'
                    : `→ ${device.label}`
            return (
              <button
                key={device.folderId}
                type="button"
                className={`btn btn-xs ${status === 'error' ? 'btn-danger' : 'btn-neutral'}`}
                disabled={status === 'pending'}
                aria-busy={status === 'pending'}
                onClick={() => onSend(row.file, device)}
              >
                {label}
              </button>
            )
          })}
        </div>
      </li>
      {expanded && (
        <li className="mb-1 rounded-lg bg-neutral-100/70 p-3 dark:bg-neutral-800/30">
          <div className="text-xs leading-relaxed text-neutral-600 sm:pl-7 dark:text-neutral-400">
            {row.description ? (
              <p>{row.description}</p>
            ) : (
              <p className="text-neutral-400 italic">No description on file.</p>
            )}
            {row.addedAt && (
              <p className="mt-1.5 text-neutral-400">
                Added {new Date(row.addedAt).toLocaleDateString()}
              </p>
            )}
            {(meta?.pages != null ||
              meta?.literaryType ||
              meta?.listsCount != null ||
              meta?.published != null ||
              meta?.audioHours != null) && (
              <p className="mt-1.5 text-neutral-400">
                {[
                  meta.published != null ? `first published ${meta.published}` : null,
                  meta.pages != null ? `${meta.pages} pages` : null,
                  meta.audioHours != null ? `~${meta.audioHours}h audio` : null,
                  meta.literaryType,
                  meta.ratingsCount != null
                    ? `${meta.ratingsCount.toLocaleString()} ratings`
                    : null,
                  meta.listsCount != null
                    ? `on ${meta.listsCount.toLocaleString()} lists`
                    : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            )}
            {meta?.genres && meta.genres.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {meta.genres.map((g) => (
                  <button
                    key={g}
                    type="button"
                    className="badge bg-brand-50 text-brand-700 hover:bg-brand-100 dark:bg-brand-950/60 dark:text-brand-300 dark:hover:bg-brand-950"
                    onClick={() => onFilterGenre(g)}
                  >
                    {g}
                  </button>
                ))}
              </div>
            )}
            {meta?.moods && meta.moods.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {meta.moods.map((m) => (
                  <button
                    key={m}
                    type="button"
                    className="badge bg-violet-50 text-violet-700 hover:bg-violet-100 dark:bg-violet-950/50 dark:text-violet-300 dark:hover:bg-violet-950"
                    onClick={() => onFilterMood?.(m)}
                  >
                    {m}
                  </button>
                ))}
              </div>
            )}
            {meta?.contentWarnings && meta.contentWarnings.length > 0 && (
              <details className="mt-1.5 text-neutral-400">
                <summary className="cursor-pointer select-none marker:text-neutral-300 hover:text-neutral-600 dark:hover:text-neutral-300">
                  Content warnings ({meta.contentWarnings.length})
                </summary>
                <p className="mt-1 text-neutral-500">{meta.contentWarnings.join(' · ')}</p>
                <p className="mt-0.5 text-[11px] text-neutral-400">Crowd-tagged on Hardcover.</p>
              </details>
            )}
            {gap && gap.missing.length > 0 && row.series && (
              <p className="mt-1.5 text-amber-700 dark:text-amber-500">
                Missing from {row.series}:{' '}
                {gap.missing
                  .map((n) => (gap.missingTitles?.[n] ? `#${n} ${gap.missingTitles[n]}` : `#${n}`))
                  .join(', ')}
                {gap.source === 'hardcover' && gap.hardcoverSlug && (
                  <>
                    {' '}
                    <a
                      href={`https://hardcover.app/series/${gap.hardcoverSlug}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
                    >
                      (via Hardcover)
                    </a>
                  </>
                )}
              </p>
            )}
            {gap?.nextUp && row.series && (
              <p className="mt-1.5 text-neutral-500">
                Next in {row.series}: #{gap.nextUp.position} {gap.nextUp.title}
              </p>
            )}
            {gap?.upcoming && gap.upcoming.length > 0 && row.series && (
              <p className="mt-1.5 text-neutral-500">
                Coming:{' '}
                {gap.upcoming
                  .map(
                    (u) =>
                      `#${u.position} ${u.title}${
                        monthYear(u.releaseDate) ? ` — ${monthYear(u.releaseDate)}` : ''
                      }`,
                  )
                  .join(', ')}
              </p>
            )}
            {(seriesPeers.length > 1 || authorPeers.length > 1) && (
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {seriesPeers.length > 1 && (
                  <button
                    className="btn btn-neutral btn-xs"
                    onClick={() => onSelectMany(seriesPeers.map((r) => r.id), true)}
                  >
                    Select all {seriesPeers.length} in {row.series}
                  </button>
                )}
                {authorPeers.length > 1 && (
                  <button
                    className="btn btn-neutral btn-xs"
                    onClick={() => onSelectMany(authorPeers.map((r) => r.id), true)}
                  >
                    Select all {authorPeers.length} by {row.author}
                  </button>
                )}
              </div>
            )}
            {onMarkRead && (
              <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-medium tracking-wide text-neutral-400 uppercase">
                  Reading status
                </span>
                {(Object.keys(READING_STATUS_LABELS) as ReadingStatus[]).map((s) => {
                  const active = row.reading?.status === s
                  return (
                    <button
                      key={s}
                      type="button"
                      className={`btn btn-xs ${active ? 'btn-primary' : 'btn-neutral'}`}
                      onClick={() => onMarkRead(row, s)}
                    >
                      {READING_STATUS_LABELS[s]}
                    </button>
                  )
                })}
                {row.reading?.pending && (
                  <span className="text-xs text-neutral-400">syncing to Hardcover…</span>
                )}
              </div>
            )}
            {recs && recs.length > 0 && (
              <ReadersAlsoLiked
                recs={recs}
                allRows={allRows}
                token={token}
                affinity={recAffinity}
                onRequestBook={onRequestBook}
              />
            )}
          </div>
        </li>
      )}
    </>
  )
}

import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { groupHeading, type BookRow as Row, type SortKey } from '../lib/books'
import type { DriveFile } from '../lib/drive'
import type { SeriesGap } from '../lib/seriesGaps'
import type { SentMap } from '../lib/sentTracker'
import type { KoboDevice } from '../lib/settings'
import { BookRow } from './BookRow'

const PAGE_SIZE = 120 // rows rendered before the "load more" sentinel grows the window

interface Props {
  rows: Row[]
  allRows: Row[]
  totalCount: number
  sort: SortKey
  token: string
  seriesGaps: Map<string, SeriesGap>
  selected: Set<string>
  expandedId: string | null
  sentMap: SentMap
  koboDevices: KoboDevice[]
  emptyMessage: string
  onToggleSelect: (id: string) => void
  onSelectMany: (ids: string[], on: boolean) => void
  onExpand: (id: string) => void
  onSend: (file: DriveFile, device: KoboDevice) => void
  onDownload: (file: DriveFile) => void
  onFilterAuthor: (author: string) => void
  onFilterSeries: (series: string) => void
}

export function BookList({
  rows,
  allRows,
  totalCount,
  sort,
  token,
  seriesGaps,
  selected,
  expandedId,
  sentMap,
  koboDevices,
  emptyMessage,
  onToggleSelect,
  onSelectMany,
  onExpand,
  onSend,
  onDownload,
  onFilterAuthor,
  onFilterSeries,
}: Props) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const visibleRows = rows.slice(0, visibleCount)

  // Row ids per group heading (for the heading's select-all box), whole
  // result set not just the rendered window.
  const groupIndex = useMemo(() => {
    const map = new Map<string, string[]>()
    if (sort === 'author' || sort === 'series') {
      for (const row of rows) {
        const heading = groupHeading(row, sort)
        if (heading == null) continue
        const ids = map.get(heading)
        if (ids) ids.push(row.id)
        else map.set(heading, [row.id])
      }
    }
    return map
  }, [rows, sort])

  useEffect(() => setVisibleCount(PAGE_SIZE), [rows])

  const sentinelRef = useRef<HTMLLIElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setVisibleCount((c) => c + PAGE_SIZE)
      },
      { rootMargin: '600px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [visibleRows.length])

  return (
    <>
      <p className="mt-4 text-xs font-medium text-neutral-400">
        {rows.length === totalCount
          ? `${totalCount} book${totalCount === 1 ? '' : 's'}`
          : `${rows.length} of ${totalCount} books`}
      </p>

      <ul className="mt-1 text-sm">
        {visibleRows.map((row, i) => {
          const heading = groupHeading(row, sort)
          const showHeading =
            heading != null && heading !== (i > 0 ? groupHeading(visibleRows[i - 1], sort) : null)
          const groupIds = showHeading ? (groupIndex.get(heading) ?? []) : []
          const groupAllSelected = groupIds.length > 0 && groupIds.every((id) => selected.has(id))
          return (
            <Fragment key={row.id}>
              {showHeading && (
                <li className="mt-3 flex items-center gap-3 border-b border-neutral-200 pb-1.5 first:mt-0 dark:border-neutral-800">
                  <input
                    type="checkbox"
                    className="accent-brand-600"
                    checked={groupAllSelected}
                    aria-label={`Select all in ${heading}`}
                    onChange={() => onSelectMany(groupIds, !groupAllSelected)}
                  />
                  <span className="text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                    {heading}
                  </span>
                  <span className="text-[11px] text-neutral-400">{groupIds.length}</span>
                </li>
              )}
              <BookRow
                row={row}
                allRows={allRows}
                token={token}
                gap={row.series ? seriesGaps.get(row.series) : undefined}
                selected={selected.has(row.id)}
                expanded={expandedId === row.id}
                sentDevices={koboDevices.filter((d) => sentMap[d.folderId]?.[row.id])}
                koboDevices={koboDevices}
                onToggleSelect={onToggleSelect}
                onExpand={onExpand}
                onSelectMany={onSelectMany}
                onSend={onSend}
                onDownload={onDownload}
                onFilterAuthor={onFilterAuthor}
                onFilterSeries={onFilterSeries}
              />
            </Fragment>
          )
        })}
        {rows.length > visibleRows.length && (
          <li ref={sentinelRef} className="py-6 text-center text-xs text-neutral-400">
            Loading more… ({visibleRows.length} of {rows.length})
          </li>
        )}
        {rows.length === 0 && (
          <li className="py-10 text-center text-sm text-neutral-400">{emptyMessage}</li>
        )}
      </ul>
    </>
  )
}

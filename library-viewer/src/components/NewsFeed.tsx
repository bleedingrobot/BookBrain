import { useState } from 'react'
import { timeAgo, type NewsItem } from '../lib/news'

const COLLAPSE_KEY = 'bookbrain.newsCollapsed'

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === 'true'
  } catch {
    return false
  }
}

// One article row — a source chip, the headline as an outbound link, a short
// excerpt, and how long ago it went up. Shared by NewsFeed and NewsScreen.
// `onDismiss` adds an X that hides the article for good on this device.
export function NewsRow({ item, onDismiss }: { item: NewsItem; onDismiss?: () => void }) {
  return (
    <li className="py-2.5">
      <div className="flex items-center gap-2">
        <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
          {item.source}
        </span>
        <span className="shrink-0 text-[11px] text-neutral-400">{timeAgo(item.published)}</span>
        {onDismiss && (
          <button
            type="button"
            aria-label="Dismiss this article"
            title="Dismiss — won't come back"
            onClick={onDismiss}
            className="ml-auto shrink-0 rounded-full border border-neutral-200 px-2 py-0.5 text-[11px] font-medium text-neutral-400 transition-colors hover:border-neutral-300 hover:bg-neutral-100 hover:text-neutral-700 dark:border-neutral-700 dark:hover:bg-neutral-800 dark:hover:text-neutral-200"
          >
            ✕ Hide
          </button>
        )}
      </div>
      <a
        href={item.link}
        target="_blank"
        rel="noreferrer"
        className="mt-0.5 block text-sm font-medium text-neutral-900 hover:text-brand-600 hover:underline dark:text-neutral-100 dark:hover:text-brand-400"
      >
        {item.title}
      </a>
      {item.summary && (
        <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-neutral-500">
          {item.summary}
        </p>
      )}
    </li>
  )
}

// The compact section that sits under the release marquees.
export function NewsFeed({
  items,
  dismissed,
  onDismiss,
  onSeeAll,
}: {
  items: NewsItem[]
  dismissed: Set<string>
  onDismiss: (link: string) => void
  onSeeAll: () => void
}) {
  const [collapsed, setCollapsed] = useState(loadCollapsed)
  const visible = items.filter((i) => !dismissed.has(i.link)).slice(0, 6)
  if (visible.length === 0) return null

  function toggle() {
    setCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem(COLLAPSE_KEY, String(next))
      } catch {
        /* private mode */
      }
      return next
    })
  }

  return (
    <section className="mb-5">
      <div className="mb-1 flex items-center justify-between">
        <button
          className="flex items-center gap-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase hover:text-neutral-700 dark:text-neutral-400 dark:hover:text-neutral-200"
          onClick={toggle}
          aria-expanded={!collapsed}
        >
          <svg
            viewBox="0 0 12 12"
            className={`h-3 w-3 transition-transform ${collapsed ? '' : 'rotate-90'}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M4 2l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          From around the SFF world
          {collapsed && <span className="ml-1 font-normal normal-case">({visible.length})</span>}
        </button>
        {!collapsed && (
          <button
            className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
            onClick={onSeeAll}
          >
            More →
          </button>
        )}
      </div>
      {!collapsed && (
        <ul className="divide-y divide-neutral-100 dark:divide-neutral-800/60">
          {visible.map((item) => (
            <NewsRow key={item.link} item={item} onDismiss={() => onDismiss(item.link)} />
          ))}
        </ul>
      )}
    </section>
  )
}

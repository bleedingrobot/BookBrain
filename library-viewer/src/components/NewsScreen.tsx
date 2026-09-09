import { useMemo, useState } from 'react'
import type { News } from '../lib/news'
import { NewsRow } from './NewsFeed'

export function NewsScreen({ news, onBack }: { news: News; onBack: () => void }) {
  const [source, setSource] = useState('all')

  const sources = useMemo(
    () => Array.from(new Set(news.items.map((i) => i.source))).sort(),
    [news.items],
  )
  const visible = source === 'all' ? news.items : news.items.filter((i) => i.source === source)

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">From around the SFF world</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        The latest from a handful of SFF news, review and short-fiction sites. Headlines link
        straight to the original — BookBrain just collects them.
      </p>

      {sources.length > 1 && (
        <div className="mt-4 flex flex-wrap gap-1.5">
          {['all', ...sources].map((s) => (
            <button
              key={s}
              onClick={() => setSource(s)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                source === s
                  ? 'border-brand-600 bg-brand-600 text-white'
                  : 'border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800'
              }`}
            >
              {s === 'all' ? 'All sources' : s}
            </button>
          ))}
        </div>
      )}

      <ul className="mt-3 divide-y divide-neutral-100 dark:divide-neutral-800/60">
        {visible.map((item) => (
          <NewsRow key={item.link} item={item} />
        ))}
        {visible.length === 0 && (
          <li className="py-6 text-sm text-neutral-400">Nothing here yet.</li>
        )}
      </ul>
    </div>
  )
}

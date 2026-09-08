import { useEffect, useState } from 'react'
import { openLibraryCoverUrl } from '../lib/covers'
import type { ReleaseItem } from '../lib/releases'

interface Result {
  // ReleaseItem.key → a verified Open Library cover URL.
  urls: Map<string, string>
  done: boolean
}

// A lighter cousin of useMarqueeCovers: the "new & upcoming" entries aren't
// in the library so there's no local thumbnail to try — it's the Open
// Library-by-ISBN path only, still verified with an Image() probe so the
// strip never renders a tile it can't fill. Entries with no ISBN, or whose
// cover doesn't resolve, are simply dropped.
export function useReleaseCovers(items: ReleaseItem[]): Result {
  const [urls, setUrls] = useState<Map<string, string>>(new Map())
  const [done, setDone] = useState(false)

  useEffect(() => {
    let cancelled = false
    const resolved = new Map<string, string>()
    setUrls(new Map())
    setDone(false)

    const commit = () => {
      if (!cancelled) setUrls(new Map(resolved))
    }

    const one = async (item: ReleaseItem) => {
      if (cancelled || !item.isbn13) return
      const url = openLibraryCoverUrl(item.isbn13)
      const ok = await new Promise<boolean>((res) => {
        const probe = new Image()
        probe.onload = () => res(probe.naturalWidth > 1)
        probe.onerror = () => res(false)
        probe.src = url
      })
      if (!cancelled && ok) {
        resolved.set(item.key, url)
        commit()
      }
    }

    Promise.allSettled(items.map(one)).then(() => {
      if (!cancelled) setDone(true)
    })

    return () => {
      cancelled = true
    }
  }, [items])

  return { urls, done }
}

import { useMemo } from 'react'
import { useReleaseCovers } from '../hooks/useReleaseCovers'
import { monthYear, type ReleaseItem } from '../lib/releases'
import { Marquee, type MarqueeCard } from './Marquee'

interface Props {
  label: string
  items: ReleaseItem[]
  // "New for you" wants at least a couple; "Coming soon" is worth showing
  // even for a single announced book.
  minCards?: number
  onPick: (item: ReleaseItem) => void
  fullscreen?: boolean
  onOpenFullscreen?: () => void
}

function caption(item: ReleaseItem): string {
  const bits = [item.title]
  if (item.series) {
    bits.push(`${item.series}${item.seriesPosition ? ` #${item.seriesPosition}` : ''}`)
  } else if (item.author) {
    bits.push(item.author)
  }
  const when = monthYear(item.releaseDate)
  if (when) bits.push(when)
  return bits.join(' — ')
}

export function ReleaseMarquee({
  label,
  items,
  minCards = 2,
  onPick,
  fullscreen,
  onOpenFullscreen,
}: Props) {
  const { urls, done } = useReleaseCovers(items)
  const byKey = useMemo(() => new Map(items.map((i) => [i.key, i])), [items])

  const cards = useMemo<MarqueeCard[]>(
    () =>
      items
        .filter((i) => urls.has(i.key))
        .map((i) => ({ key: i.key, url: urls.get(i.key)!, caption: caption(i) })),
    [items, urls],
  )

  if (items.length === 0) return null

  return (
    <Marquee
      label={label}
      cards={cards}
      loading={!done}
      minCards={minCards}
      fullscreen={fullscreen}
      onOpenFullscreen={onOpenFullscreen}
      onPick={(key) => {
        const item = byKey.get(key)
        if (item) onPick(item)
      }}
    />
  )
}

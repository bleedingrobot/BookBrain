import { useMemo } from 'react'
import type { BookRow } from '../lib/books'
import { useMarqueeCovers } from '../hooks/useMarqueeCovers'
import { Marquee, type MarqueeCard } from './Marquee'

interface Props {
  books: BookRow[]
  token: string
  onPick: (id: string) => void
  fullscreen?: boolean
  onOpenFullscreen?: () => void
}

export function RecentMarquee({ books, token, onPick, fullscreen, onOpenFullscreen }: Props) {
  const { urls, done } = useMarqueeCovers(books, token)

  // Only books with a resolved, known-good cover — never a blank tile.
  const cards = useMemo<MarqueeCard[]>(
    () =>
      books
        .filter((b) => urls.has(b.id))
        .map((b) => ({
          key: b.id,
          url: urls.get(b.id)!,
          caption: `${b.title}${b.author ? ` — ${b.author}` : ''}`,
        })),
    [books, urls],
  )

  if (books.length < 2) return null

  return (
    <Marquee
      label="Recently added"
      cards={cards}
      loading={!done}
      onPick={onPick}
      fullscreen={fullscreen}
      onOpenFullscreen={onOpenFullscreen}
    />
  )
}

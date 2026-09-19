export interface ResolvedBook {
  title: string
  author: string | null
  series: string | null
  series_number: number | null
  isbn13: string | null
  cover_url: string | null
  note: string | null
}

export interface LibraryMatch {
  file_id: number
  filename: string
  status: string
}

export interface ResolveResult {
  found: boolean
  resolved: ResolvedBook | null
  already_in_library: LibraryMatch | null
  already_on_wishlist: boolean
  note: string | null
}

export interface WishlistItemCreate extends ResolvedBook {
  raw_request: string
}

export interface WishlistItem {
  id: number
  raw_request: string
  title: string
  author: string | null
  series: string | null
  series_number: number | null
  isbn13: string | null
  cover_url: string | null
  note: string | null
  status: 'wanted' | 'acquired'
  acquired_at: string | null
  acquired_file_id: number | null
  created_at: string
}

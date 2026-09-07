import { useState } from 'react'

interface Props {
  busy: boolean
  hasKobo: boolean
  onRefresh: () => void
  onRebuild: () => void
  onShowDevices: () => void
  onShowWishlist: () => void
  onShowActivity: () => void
  onShare: () => void
  onCopyLink: () => void
  onEditSettings: () => void
  onShowSetup: () => void
  onForget: () => void
  offlineCount: number
  onClearDownloads: () => void
}

export function LibraryHeader({
  busy,
  hasKobo,
  onRefresh,
  onRebuild,
  onShowDevices,
  onShowWishlist,
  onShowActivity,
  onShare,
  onCopyLink,
  onEditSettings,
  onShowSetup,
  onForget,
  offlineCount,
  onClearDownloads,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false)

  const items = [
    { label: 'Activity', fn: onShowActivity },
    { label: 'Wishlist', fn: onShowWishlist },
    { label: 'Rebuild library', fn: onRebuild, disabled: busy },
    ...(offlineCount > 0
      ? [{ label: `Clear downloaded books (${offlineCount})`, fn: onClearDownloads }]
      : []),
    { label: 'Share…', fn: onShare },
    { label: 'Copy link', fn: onCopyLink },
    { label: 'Change settings', fn: onEditSettings },
    { label: 'Recovery checklist', fn: onShowSetup },
  ]

  return (
    <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-4">
      <div className="flex items-center gap-2.5">
        <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="h-6 w-6" />
        <h1 className="text-lg font-semibold tracking-tight">BookBrain Library</h1>
      </div>
      <div className="flex items-center gap-1">
        <button className="btn btn-ghost" disabled={busy} onClick={onRefresh}>
          {busy ? 'Syncing…' : 'Refresh'}
        </button>
        {hasKobo && (
          <button className="btn btn-ghost" onClick={onShowDevices}>
            On devices
          </button>
        )}
        <div className="relative">
          <button
            className="btn btn-ghost px-2"
            aria-label="More actions"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            <span className="text-base leading-none">⋯</span>
          </button>
          {menuOpen && (
            <>
              <button
                className="fixed inset-0 z-30 cursor-default"
                aria-hidden
                tabIndex={-1}
                onClick={() => setMenuOpen(false)}
              />
              <div className="card absolute right-0 z-40 mt-1 w-44 overflow-hidden p-1 shadow-lg">
                {items.map((item) => (
                  <button
                    key={item.label}
                    disabled={item.disabled}
                    className="block w-full rounded px-2.5 py-1.5 text-left text-xs text-neutral-700 hover:bg-neutral-100 disabled:opacity-50 dark:text-neutral-200 dark:hover:bg-neutral-800"
                    onClick={() => {
                      setMenuOpen(false)
                      item.fn()
                    }}
                  >
                    {item.label}
                  </button>
                ))}
                <div className="my-1 border-t border-neutral-200 dark:border-neutral-800" />
                <button
                  className="block w-full rounded px-2.5 py-1.5 text-left text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
                  onClick={() => {
                    setMenuOpen(false)
                    onForget()
                  }}
                >
                  Forget this device
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  )
}

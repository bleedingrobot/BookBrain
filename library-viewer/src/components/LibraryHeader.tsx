interface Props {
  busy: boolean
  onOpenDrawer: () => void
}

// The persistent top bar above the shelf. Navigation and account/admin
// actions live in <Sidebar> and <SettingsScreen> now — this is just the
// logo, the mobile hamburger, and a sync status line.
export function LibraryHeader({ busy, onOpenDrawer }: Props) {
  return (
    <header className="flex items-center gap-3 py-4">
      <button
        type="button"
        className="btn btn-ghost px-2 lg:hidden"
        aria-label="Open menu"
        onClick={onOpenDrawer}
      >
        <span className="text-base leading-none">☰</span>
      </button>
      <div className="flex flex-1 items-center gap-2.5">
        <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="h-6 w-6 lg:hidden" />
        <h1 className="text-lg font-semibold tracking-tight lg:hidden">BookBrain Library</h1>
      </div>
      {busy && <span className="text-xs text-neutral-400">Syncing…</span>}
    </header>
  )
}

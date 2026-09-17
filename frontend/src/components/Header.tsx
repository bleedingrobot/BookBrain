interface Props {
  onOpenDrawer: () => void
}

// The top bar above page content. Navigation lives in <Sidebar> now — this
// is just the mobile hamburger plus the logo (hidden on desktop, where the
// sidebar already shows the wordmark).
export function Header({ onOpenDrawer }: Props) {
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
      <div className="flex flex-1 items-center gap-2.5 lg:hidden">
        <img src="/favicon.svg" alt="" className="h-6 w-6" />
        <h1 className="text-lg font-semibold tracking-tight">BookBrain Admin</h1>
      </div>
    </header>
  )
}

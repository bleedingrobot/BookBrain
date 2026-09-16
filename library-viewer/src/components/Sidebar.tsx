import type { ViewerSettings } from '../lib/settings'

// A single "Display" toggle row: label, one-line description, and a switch.
// Own component (not a checkbox) so it reads as "control what's on the
// shelf" rather than a settings form — that's the whole point of moving
// these out of SettingsForm and into the sidebar.
function ToggleRow({
  label,
  description,
  checked,
  onChange,
}: {
  label: string
  description?: string
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex items-start gap-2.5 rounded-md px-2 py-1.5 hover:bg-neutral-100 dark:hover:bg-neutral-800/60">
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-neutral-700 dark:text-neutral-200">{label}</span>
        {description && (
          <span className="mt-0.5 block text-xs text-neutral-400">{description}</span>
        )}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors ${
          checked ? 'bg-brand-600' : 'bg-neutral-300 dark:bg-neutral-700'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-4.5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </label>
  )
}

function NavRow({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="block w-full rounded-md px-2 py-1.5 text-left text-sm text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800/60"
    >
      {label}
    </button>
  )
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="px-2 pb-1 text-[11px] font-semibold tracking-wide text-neutral-400 uppercase">
      {children}
    </h2>
  )
}

interface Props {
  settings: ViewerSettings
  onToggle: (key: keyof ViewerSettings, value: boolean) => void
  hasKobo: boolean
  hasReading: boolean
  hasPrompts: boolean
  hasCollections: boolean
  onShowDashboard: () => void
  onShowWishlist: () => void
  onShowActivity: () => void
  onShowNews: () => void
  onShowStats: () => void
  onShowPrompts: () => void
  onShowCollections: () => void
  onShowDevices: () => void
  onOpenSettings: () => void
  open: boolean
  onClose: () => void
}

const DISPLAY_TOGGLES: {
  key: keyof ViewerSettings
  label: string
  description: string
}[] = [
  {
    key: 'showReadingGoal',
    label: 'Reading goal bar',
    description: 'This year’s reading-goal progress bar, when one is set.',
  },
  {
    key: 'showRecentlyAdded',
    label: 'Recently added',
    description: 'Covers for the books organized into your library most recently.',
  },
  {
    key: 'showNewForYou',
    label: 'New for you',
    description: 'New releases from series and authors already in your library.',
  },
  {
    key: 'showComingSoon',
    label: 'Coming soon',
    description: 'Announced but unreleased books from your series and authors.',
  },
  {
    key: 'showGlobalReleases',
    label: 'Most anticipated',
    description: 'Hardcover’s most-wanted upcoming books overall.',
  },
  {
    key: 'showTrending',
    label: 'Trending on Hardcover',
    description: 'What the Hardcover community is reading right now.',
  },
  {
    key: 'showContinueReading',
    label: 'Continue reading',
    description: 'Books you’re partway through.',
  },
  {
    key: 'showReadNext',
    label: 'Read next',
    description: 'The next unread book in series you’re following.',
  },
  {
    key: 'showShowcase',
    label: 'Recommended for you',
    description: 'A rotating featured pick at the bottom of the shelf.',
  },
  {
    key: 'showNews',
    label: 'SFF news feed',
    description: 'Latest headlines from Reactor, Locus, File 770 and more.',
  },
]

function SidebarContent({
  settings,
  onToggle,
  hasKobo,
  hasReading,
  hasPrompts,
  hasCollections,
  onShowDashboard,
  onShowWishlist,
  onShowActivity,
  onShowNews,
  onShowStats,
  onShowPrompts,
  onShowCollections,
  onShowDevices,
  onOpenSettings,
}: Omit<Props, 'open' | 'onClose'>) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-3 pt-4 pb-3">
        <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="h-6 w-6" />
        <h1 className="text-lg font-semibold tracking-tight">BookBrain</h1>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        <GroupLabel>Display</GroupLabel>
        <div className="mb-4 space-y-0.5">
          {DISPLAY_TOGGLES.map((t) => (
            <ToggleRow
              key={t.key}
              label={t.label}
              description={t.description}
              checked={settings[t.key] !== false}
              onChange={(value) => onToggle(t.key, value)}
            />
          ))}
        </div>

        <GroupLabel>Screens</GroupLabel>
        <div className="space-y-0.5">
          <NavRow label="Dashboard" onClick={onShowDashboard} />
          <NavRow label="Wishlist & requests" onClick={onShowWishlist} />
          <NavRow label="Activity" onClick={onShowActivity} />
          {hasReading && <NavRow label="Reading stats" onClick={onShowStats} />}
          {hasPrompts && <NavRow label="Your library answers" onClick={onShowPrompts} />}
          {hasCollections && <NavRow label="Collections" onClick={onShowCollections} />}
          <NavRow label="SFF news" onClick={onShowNews} />
          {hasKobo && <NavRow label="On devices" onClick={onShowDevices} />}
        </div>
      </div>

      <div className="border-t border-neutral-200 px-2 py-2 dark:border-neutral-800">
        <NavRow label="⚙ Settings" onClick={onOpenSettings} />
      </div>
    </div>
  )
}

export function Sidebar(props: Props) {
  const { open, onClose } = props
  return (
    <>
      {/* Desktop/tablet: a persistent left rail. */}
      <aside className="card hidden shrink-0 lg:sticky lg:top-0 lg:z-10 lg:flex lg:h-screen lg:w-64 lg:rounded-none lg:border-y-0 lg:border-l-0">
        <SidebarContent {...props} />
      </aside>

      {/* Mobile: a slide-out drawer behind the header's hamburger button. */}
      <div className={`fixed inset-0 z-40 lg:hidden ${open ? '' : 'pointer-events-none'}`}>
        <button
          type="button"
          aria-hidden={!open}
          tabIndex={-1}
          onClick={onClose}
          className={`absolute inset-0 bg-neutral-950/50 backdrop-blur-sm transition-opacity ${
            open ? 'opacity-100' : 'opacity-0'
          }`}
        />
        <aside
          className={`card absolute inset-y-0 left-0 w-72 rounded-none border-y-0 border-l-0 transition-transform ${
            open ? 'translate-x-0' : '-translate-x-full'
          }`}
        >
          <SidebarContent {...props} />
        </aside>
      </div>
    </>
  )
}

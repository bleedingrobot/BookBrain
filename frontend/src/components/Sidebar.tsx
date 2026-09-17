import { NavLink } from 'react-router-dom'

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="px-2 pb-1 text-[11px] font-semibold tracking-wide text-neutral-400 uppercase">
      {children}
    </h2>
  )
}

function NavRow({
  to,
  label,
  end,
  onClick,
}: {
  to: string
  label: string
  end?: boolean
  onClick?: () => void
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onClick}
      className={({ isActive }) =>
        `block rounded-md px-2 py-1.5 text-sm ${
          isActive
            ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300'
            : 'text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800/60'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

const GROUPS: { label: string; links: { to: string; label: string; end?: boolean }[] }[] = [
  {
    label: 'Pipeline',
    links: [
      { to: '/', label: 'Dashboard', end: true },
      { to: '/inbox', label: 'Inbox' },
      { to: '/review', label: 'Review Queue' },
      { to: '/activity', label: 'Activity' },
    ],
  },
  {
    label: 'Library',
    links: [
      { to: '/library', label: 'Library' },
      { to: '/smart-collections', label: 'Smart Collections' },
      { to: '/duplicates', label: 'Duplicates' },
      { to: '/library-audit', label: 'Library Audit' },
    ],
  },
  {
    label: 'Acquire',
    links: [
      { to: '/wishlist', label: 'Wishlist' },
      { to: '/acquire', label: 'Find a Book' },
    ],
  },
]

interface Props {
  open: boolean
  onClose: () => void
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-3 pt-4 pb-3">
        <img src="/favicon.svg" alt="" className="h-6 w-6" />
        <h1 className="text-lg font-semibold tracking-tight">BookBrain Admin</h1>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-2 pb-3">
        {GROUPS.map((group) => (
          <div key={group.label}>
            <GroupLabel>{group.label}</GroupLabel>
            <div className="space-y-0.5">
              {group.links.map((link) => (
                <NavRow key={link.to} {...link} onClick={onNavigate} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-neutral-200 px-2 py-2 dark:border-neutral-800">
        <NavRow to="/settings" label="⚙ Settings" onClick={onNavigate} />
      </div>
    </div>
  )
}

export function Sidebar({ open, onClose }: Props) {
  return (
    <>
      {/* Desktop/tablet: a persistent left rail. */}
      <aside className="card hidden shrink-0 lg:sticky lg:top-0 lg:z-10 lg:flex lg:h-screen lg:w-64 lg:rounded-none lg:border-y-0 lg:border-l-0">
        <SidebarContent />
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
          <SidebarContent onNavigate={onClose} />
        </aside>
      </div>
    </>
  )
}

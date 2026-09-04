import { NavLink } from 'react-router-dom'

const LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/inbox', label: 'Inbox' },
  { to: '/review', label: 'Review Queue' },
  { to: '/library', label: 'Library' },
  { to: '/wishlist', label: 'Wishlist' },
  { to: '/library-audit', label: 'Library Audit' },
  { to: '/duplicates', label: 'Duplicates' },
  { to: '/activity', label: 'Activity' },
  { to: '/settings', label: 'Settings' },
]

export function NavBar() {
  return (
    <nav className="flex gap-4 border-b border-neutral-200 px-6 py-3 text-sm dark:border-neutral-800">
      {LINKS.map((link) => (
        <NavLink
          key={link.to}
          to={link.to}
          end={link.to === '/'}
          className={({ isActive }) =>
            isActive
              ? 'font-medium text-neutral-900 dark:text-neutral-100'
              : 'text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100'
          }
        >
          {link.label}
        </NavLink>
      ))}
    </nav>
  )
}

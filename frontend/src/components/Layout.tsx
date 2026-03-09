import { NavLink, Outlet } from 'react-router-dom'

const navItems = [
  { to: '/', label: 'Dashboard' },
  { to: '/workout', label: 'Workout' },
  { to: '/log', label: 'Chat' },
  { to: '/tissues', label: 'Tissues' },
]

export default function Layout() {
  const gitCommit = (import.meta.env.VITE_GIT_COMMIT as string | undefined)?.trim()
  const shortCommit = gitCommit ? gitCommit.slice(0, 6) : undefined

  return (
    <div className="flex flex-col h-[100dvh] bg-gray-50 pt-[var(--safe-top)] md:pt-0">

      {/* Desktop nav links */}
      <nav className="hidden md:block bg-white border-b border-gray-200">
        <div className="max-w-5xl mx-auto px-4 flex items-center gap-6 h-14">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `text-sm ${isActive ? 'text-blue-600 font-medium' : 'text-gray-600 hover:text-gray-900'}`
              }
            >
              {item.label}
            </NavLink>
          ))}
          {shortCommit ? (
            <span className="ml-auto text-[10px] font-mono text-gray-400" title={gitCommit}>
              {shortCommit}
            </span>
          ) : null}
        </div>
      </nav>

      {/* Main content — flex-1 fills between top and bottom nav */}
      <main className="flex-1 min-h-0 max-w-5xl w-full mx-auto px-4 pt-1 md:pt-6 pb-1 md:pb-6">
        <Outlet />
      </main>

      {/* Mobile bottom tab bar — hidden on md+ */}
      <nav className="shrink-0 bg-white border-t border-gray-200 md:hidden z-50">
        <div className="flex">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex-1 flex items-center justify-center h-14 text-xs font-medium ${
                  isActive ? 'text-blue-600' : 'text-gray-500'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </div>
        <div className="pb-[var(--safe-bottom)] text-center">
          {shortCommit ? (
            <span className="text-[10px] font-mono text-gray-400" title={gitCommit}>
              {shortCommit}
            </span>
          ) : null}
        </div>
      </nav>
    </div>
  )
}

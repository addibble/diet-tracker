import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { logout } from '../api'

const navItems = [
  { to: '/', label: 'Dashboard' },
  { to: '/foods', label: 'Foods' },
  { to: '/recipes', label: 'Recipes' },
  { to: '/log', label: 'Log Meal' },
]

export default function Layout() {
  const navigate = useNavigate()
  const gitCommit = (import.meta.env.VITE_GIT_COMMIT as string | undefined)?.trim()
  const shortCommit = gitCommit ? gitCommit.slice(0, 6) : undefined

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top nav — pt accounts for Dynamic Island / notch safe area */}
      <nav className="bg-white border-b border-gray-200 pt-[var(--safe-top)]">
        <div className="max-w-5xl mx-auto px-4 flex items-center justify-between h-14">
          <div className="flex items-center gap-6">
            <span className="font-semibold text-gray-900">Diet Tracker</span>
            {/* Desktop nav links — hidden on mobile, replaced by bottom tab bar */}
            <div className="hidden md:flex items-center gap-6">
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
            </div>
          </div>
          <div className="flex items-center gap-3">
            {shortCommit ? (
              <span className="text-xs font-mono text-gray-400" title={gitCommit}>
                {shortCommit}
              </span>
            ) : null}
            <button
              onClick={handleLogout}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Logout
            </button>
          </div>
        </div>
      </nav>

      {/* Main content — on mobile, reserve space at bottom for the tab bar */}
      <main className="max-w-5xl mx-auto px-4 pt-4 md:pt-6 pb-[calc(3.5rem_+_var(--safe-bottom))] md:pb-6">
        <Outlet />
      </main>

      {/* Mobile bottom tab bar — hidden on md+ */}
      <nav className="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 md:hidden z-50">
        <div className="flex pb-[var(--safe-bottom)]">
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
      </nav>
    </div>
  )
}

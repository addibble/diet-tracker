import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  addPasskey,
  deletePasskey,
  logout,
  me,
  revokeSession,
  type Me,
} from '../api'

export default function AccountPage() {
  const [state, setState] = useState<Me | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  const reload = useCallback(async () => {
    try {
      setState(await me())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load account')
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const data = await me()
        if (!cancelled) setState(data)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load account')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const handleAdd = async () => {
    setError('')
    setBusy(true)
    try {
      const nickname = window.prompt('Nickname for this passkey (optional):') || undefined
      await addPasskey(nickname)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Add passkey failed')
    } finally {
      setBusy(false)
    }
  }

  const handleDeletePasskey = async (id: number) => {
    if (!window.confirm('Remove this passkey? You must keep at least one.')) return
    try {
      await deletePasskey(id)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete passkey failed')
    }
  }

  const handleRevokeSession = async (hash: string) => {
    try {
      await revokeSession(hash)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Revoke failed')
    }
  }

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  if (!state) return <p className="p-4 text-sm text-gray-500">Loading…</p>

  return (
    <div className="p-4 max-w-2xl mx-auto">
      <h1 className="text-xl font-semibold mb-2">Account</h1>
      {error && <p className="text-red-500 text-sm mb-4">{error}</p>}
      <div className="bg-white border rounded-md p-4 mb-4">
        <div className="text-sm text-gray-600">Signed in as</div>
        <div className="font-medium">{state.user.display_name}</div>
        <div className="text-sm text-gray-500">{state.user.email}</div>
        {state.user.is_admin && (
          <div className="text-xs text-blue-700 mt-1">Administrator</div>
        )}
        <button
          onClick={handleLogout}
          className="mt-3 text-sm text-red-600 hover:underline"
        >
          Log out
        </button>
      </div>

      <div className="bg-white border rounded-md p-4 mb-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-medium">Passkeys</h2>
          <button
            onClick={handleAdd}
            disabled={busy}
            className="text-sm text-blue-600 hover:underline disabled:text-gray-400"
          >
            + Add passkey
          </button>
        </div>
        <ul className="divide-y">
          {state.passkeys.map((p) => (
            <li key={p.id} className="py-2 flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">{p.nickname || 'Passkey'}</div>
                <div className="text-xs text-gray-500">
                  Added {new Date(p.created_at).toLocaleDateString()}
                  {p.last_used_at &&
                    ` · last used ${new Date(p.last_used_at).toLocaleDateString()}`}
                </div>
              </div>
              <button
                onClick={() => handleDeletePasskey(p.id)}
                className="text-xs text-red-600 hover:underline"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="bg-white border rounded-md p-4">
        <h2 className="font-medium mb-2">Active sessions</h2>
        <ul className="divide-y">
          {state.sessions.map((s) => (
            <li key={s.token_hash} className="py-2 flex items-center justify-between">
              <div className="text-sm">
                <div className="text-gray-700">
                  {s.user_agent || 'Unknown device'}
                </div>
                <div className="text-xs text-gray-500">
                  last seen {new Date(s.last_seen_at).toLocaleString()}
                </div>
              </div>
              <button
                onClick={() => handleRevokeSession(s.token_hash)}
                className="text-xs text-red-600 hover:underline"
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

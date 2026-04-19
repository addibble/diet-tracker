import { useCallback, useEffect, useState } from 'react'
import { request } from '../api/_request'

interface AdminUser {
  id: string
  email: string
  display_name: string
  is_admin: boolean
  created_at: string
  last_login_at: string | null
  disabled_at: string | null
  passkey_count: number
}

interface AdminInvite {
  id: number
  email_hint: string | null
  created_at: string
  expires_at: string
  consumed_at: string | null
  consumed_by: string | null
  is_bootstrap: boolean
}

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [invites, setInvites] = useState<AdminInvite[]>([])
  const [error, setError] = useState('')
  const [createdUrl, setCreatedUrl] = useState('')

  const reload = useCallback(async () => {
    try {
      const [u, i] = await Promise.all([
        request<AdminUser[]>('/admin/users'),
        request<AdminInvite[]>('/admin/invites'),
      ])
      setUsers(u)
      setInvites(i)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed')
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [u, i] = await Promise.all([
          request<AdminUser[]>('/admin/users'),
          request<AdminInvite[]>('/admin/invites'),
        ])
        if (!cancelled) {
          setUsers(u)
          setInvites(i)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const createInvite = async () => {
    setError('')
    setCreatedUrl('')
    const hint = window.prompt('Email hint (optional):') || undefined
    try {
      const r = await request<{ url: string }>('/admin/invites', {
        method: 'POST',
        body: JSON.stringify({ email_hint: hint ?? null }),
      })
      setCreatedUrl(r.url)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invite failed')
    }
  }

  const disableUser = async (id: string) => {
    if (!window.confirm('Disable this user? They will be signed out.')) return
    await request(`/admin/users/${id}/disable`, { method: 'POST' })
    await reload()
  }

  const enableUser = async (id: string) => {
    await request(`/admin/users/${id}/enable`, { method: 'POST' })
    await reload()
  }

  const deleteUser = async (id: string, email: string) => {
    const confirm = window.prompt(
      `Type ${email} to confirm permanent deletion:`,
    )
    if (confirm !== email) return
    try {
      await request(`/admin/users/${id}/delete`, {
        method: 'POST',
        body: JSON.stringify({ email_confirm: email }),
      })
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const revokeInvite = async (id: number) => {
    await request(`/admin/invites/${id}`, { method: 'DELETE' })
    await reload()
  }

  return (
    <div className="p-4 max-w-3xl mx-auto space-y-6">
      <h1 className="text-xl font-semibold">Administration</h1>
      {error && <p className="text-red-500 text-sm">{error}</p>}

      <section className="bg-white border rounded-md p-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-medium">Users ({users.length})</h2>
        </div>
        <table className="w-full text-sm">
          <thead className="text-left text-gray-500">
            <tr>
              <th className="py-1">Email</th>
              <th>Passkeys</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {users.map((u) => (
              <tr key={u.id}>
                <td className="py-2">
                  {u.email}
                  {u.is_admin && (
                    <span className="ml-1 text-xs text-blue-700">admin</span>
                  )}
                </td>
                <td>{u.passkey_count}</td>
                <td>
                  {u.disabled_at ? (
                    <span className="text-red-600">disabled</span>
                  ) : (
                    <span className="text-green-700">active</span>
                  )}
                </td>
                <td className="text-right space-x-2">
                  {u.disabled_at ? (
                    <button
                      onClick={() => enableUser(u.id)}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      Enable
                    </button>
                  ) : (
                    <button
                      onClick={() => disableUser(u.id)}
                      className="text-xs text-orange-600 hover:underline"
                    >
                      Disable
                    </button>
                  )}
                  <button
                    onClick={() => deleteUser(u.id, u.email)}
                    className="text-xs text-red-600 hover:underline"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="bg-white border rounded-md p-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-medium">Invites</h2>
          <button
            onClick={createInvite}
            className="text-sm text-blue-600 hover:underline"
          >
            + New invite
          </button>
        </div>
        {createdUrl && (
          <div className="bg-yellow-50 border border-yellow-200 p-2 text-xs mb-2 break-all">
            Share this URL <em>once</em>:
            <div className="font-mono mt-1">{createdUrl}</div>
          </div>
        )}
        <ul className="divide-y text-sm">
          {invites.map((i) => (
            <li key={i.id} className="py-2 flex items-center justify-between">
              <div>
                <div>
                  {i.email_hint || '(no hint)'}
                  {i.is_bootstrap && (
                    <span className="ml-1 text-xs text-orange-700">
                      bootstrap
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-500">
                  expires {new Date(i.expires_at).toLocaleDateString()}
                  {i.consumed_at
                    ? ` · consumed ${new Date(i.consumed_at).toLocaleDateString()}`
                    : ''}
                </div>
              </div>
              {!i.consumed_at && (
                <button
                  onClick={() => revokeInvite(i.id)}
                  className="text-xs text-red-600 hover:underline"
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

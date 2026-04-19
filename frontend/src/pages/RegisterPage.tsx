import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { registerWithInvite } from '../api'

export default function RegisterPage() {
  const { token } = useParams<{ token: string }>()
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!token) {
      setError('Missing invite token')
      return
    }
    setError('')
    setBusy(true)
    try {
      await registerWithInvite(token, email, displayName || email.split('@')[0])
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <form
        onSubmit={handleSubmit}
        className="bg-white p-8 rounded-lg shadow-sm border border-gray-200 w-96"
      >
        <h1 className="text-xl font-semibold text-gray-900 mb-2">Create account</h1>
        <p className="text-sm text-gray-500 mb-6">
          You were invited to Diet Tracker. Finish setup by creating a passkey.
        </p>
        {error && <p className="text-red-500 text-sm mb-4">{error}</p>}
        <label className="block text-sm text-gray-700 mb-1">Email</label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm mb-3"
          required
          autoFocus
        />
        <label className="block text-sm text-gray-700 mb-1">Display name (optional)</label>
        <input
          type="text"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="w-full mt-4 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:bg-gray-400"
        >
          {busy ? 'Creating passkey…' : 'Create passkey & register'}
        </button>
      </form>
    </div>
  )
}

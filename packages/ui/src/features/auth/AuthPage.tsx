import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authApi } from '../../api/auth'
import { EmptyState, LoadingSpinner } from '../../components/ui'
import type { ApiKey, CreateApiKeyRequest, NewApiKeyResponse } from '../../types/auth'

// ---- helpers ----------------------------------------------------------------

function formatDate(iso?: string): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function truncate(s: string, n = 20): string {
  return s.length > n ? `${s.slice(0, n)}...` : s
}

// ---- Create API Key modal ----------------------------------------------------

interface CreateKeyModalProps {
  onClose: () => void
  onCreated: (result: NewApiKeyResponse) => void
}

function CreateKeyModal({ onClose, onCreated }: CreateKeyModalProps): JSX.Element {
  const [principalId, setPrincipalId] = useState('')
  const [name, setName] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [error, setError] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: (req: CreateApiKeyRequest) => authApi.createApiKey(req),
    onSuccess: (data) => onCreated(data),
    onError: (e: Error) => setError(e.message),
  })

  function handleSubmit(ev: React.FormEvent): void {
    ev.preventDefault()
    if (!principalId.trim() || !name.trim()) {
      setError('Principal ID and Name are required.')
      return
    }
    setError(null)
    createMutation.mutate({
      principal_id: principalId.trim(),
      name: name.trim(),
      expires_at: expiresAt || undefined,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
        <h3 className="text-base font-semibold text-gray-900">Create API Key</h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Principal ID</label>
            <input
              type="text"
              value={principalId}
              onChange={(e) => setPrincipalId(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="user:alice"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="CI deploy key"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Expires At (optional)</label>
            <input
              type="datetime-local"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating...' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---- New key result dialog ---------------------------------------------------

interface NewKeyDialogProps {
  result: NewApiKeyResponse
  onClose: () => void
}

function NewKeyDialog({ result, onClose }: NewKeyDialogProps): JSX.Element {
  const [copied, setCopied] = useState(false)

  function handleCopy(): void {
    void navigator.clipboard.writeText(result.raw_key).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6 space-y-4">
        <h3 className="text-base font-semibold text-gray-900">API Key Created</h3>
        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
          Copy this key now. It will not be shown again.
        </p>
        <div className="bg-gray-50 border border-gray-200 rounded-md px-3 py-2 flex items-center justify-between gap-2">
          <code className="text-xs text-gray-800 break-all">{result.raw_key}</code>
          <button
            type="button"
            onClick={handleCopy}
            className="shrink-0 px-3 py-1 text-xs bg-gray-200 rounded hover:bg-gray-300"
          >
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
        <dl className="text-sm text-gray-600 space-y-1">
          <div className="flex gap-2">
            <dt className="font-medium">Key ID:</dt>
            <dd>{result.key_id}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="font-medium">Principal:</dt>
            <dd>{result.principal_id}</dd>
          </div>
          {result.name && (
            <div className="flex gap-2">
              <dt className="font-medium">Name:</dt>
              <dd>{result.name}</dd>
            </div>
          )}
        </dl>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

// ---- AuthPage (API Keys only) -----------------------------------------------

export function AuthPage(): JSX.Element {
  const queryClient = useQueryClient()
  const [principalInput, setPrincipalInput] = useState('')
  const [searchPrincipal, setSearchPrincipal] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [newKeyResult, setNewKeyResult] = useState<NewApiKeyResponse | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['auth', 'keys', searchPrincipal],
    queryFn: () => authApi.listApiKeys(searchPrincipal || undefined),
    enabled: searchPrincipal.length > 0,
  })

  const revokeMutation = useMutation({
    mutationFn: (keyId: string) => authApi.revokeApiKey(keyId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['auth', 'keys'] }),
  })

  const keys: ApiKey[] = data?.keys ?? []

  function handleSearch(ev: React.FormEvent): void {
    ev.preventDefault()
    setSearchPrincipal(principalInput.trim())
  }

  function handleCreated(result: NewApiKeyResponse): void {
    setShowCreate(false)
    setNewKeyResult(result)
    queryClient.invalidateQueries({ queryKey: ['auth', 'keys'] })
  }

  return (
    <div className="p-6 space-y-4">
      <h2 className="text-lg font-semibold text-gray-900">API Keys</h2>

      {showCreate && <CreateKeyModal onClose={() => setShowCreate(false)} onCreated={handleCreated} />}
      {newKeyResult && <NewKeyDialog result={newKeyResult} onClose={() => setNewKeyResult(null)} />}

      <div className="flex items-center justify-between gap-3">
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            value={principalInput}
            onChange={(e) => setPrincipalInput(e.target.value)}
            placeholder="Filter by principal ID..."
            className="border border-gray-300 rounded-md px-3 py-1.5 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            className="px-3 py-1.5 text-sm bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Search
          </button>
        </form>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          Create Key
        </button>
      </div>

      {!searchPrincipal ? (
        <EmptyState message="Enter a principal ID above and press Search to list API keys." />
      ) : isLoading ? (
        <div className="flex justify-center py-8">
          <LoadingSpinner size={28} />
        </div>
      ) : error ? (
        <p className="text-sm text-red-600">Failed to load API keys.</p>
      ) : keys.length === 0 ? (
        <EmptyState message={`No API keys found for "${searchPrincipal}".`} />
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Name</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Key ID</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Principal</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Created</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Expires</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Status</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.key_id} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="py-2 px-3 text-sm text-gray-900">{key.name ?? '-'}</td>
                  <td className="py-2 px-3 text-sm font-mono text-gray-600">{truncate(key.key_id, 16)}</td>
                  <td className="py-2 px-3 text-sm text-gray-600">{key.principal_id}</td>
                  <td className="py-2 px-3 text-sm text-gray-500">{formatDate(key.created_at)}</td>
                  <td className="py-2 px-3 text-sm text-gray-500">{formatDate(key.expires_at)}</td>
                  <td className="py-2 px-3 text-sm">
                    {key.revoked ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                        Revoked
                      </span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                        Active
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3">
                    <button
                      type="button"
                      disabled={!!key.revoked || revokeMutation.isPending}
                      onClick={() => revokeMutation.mutate(key.key_id)}
                      className="text-xs text-red-600 hover:underline disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

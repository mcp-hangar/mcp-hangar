import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { authApi } from '../../api/auth'
import { modalVariants, overlayVariants } from '../../lib/animations'
import { EmptyState, LoadingSpinner, PageContainer } from '../../components/ui'
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
    <motion.div
      variants={overlayVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      className="fixed inset-0 bg-overlay flex items-center justify-center z-50"
    >
      <motion.div
        variants={modalVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        className="bg-surface rounded-xl shadow-xl w-full max-w-md p-6 space-y-4"
      >
        <h3 className="text-base font-semibold text-text-primary">Create API Key</h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Principal ID</label>
            <input
              type="text"
              value={principalId}
              onChange={(e) => setPrincipalId(e.target.value)}
              className="w-full border border-border-strong rounded-lg px-3 py-1.5 text-sm bg-surface focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="user:alice"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border border-border-strong rounded-lg px-3 py-1.5 text-sm bg-surface focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="CI deploy key"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Expires At (optional)</label>
            <input
              type="datetime-local"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              className="w-full border border-border-strong rounded-lg px-3 py-1.5 text-sm bg-surface focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
          {error && <p className="text-sm text-danger">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-1.5 text-sm border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="px-4 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating...' : 'Create'}
            </button>
          </div>
        </form>
      </motion.div>
    </motion.div>
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
    <motion.div
      variants={overlayVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      className="fixed inset-0 bg-overlay flex items-center justify-center z-50"
    >
      <motion.div
        variants={modalVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        className="bg-surface rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4"
      >
        <h3 className="text-base font-semibold text-text-primary">API Key Created</h3>
        <p className="text-sm text-warning-text bg-warning-surface border border-warning rounded-lg px-3 py-2">
          Copy this key now. It will not be shown again.
        </p>
        <div className="bg-surface-secondary border border-border rounded-lg px-3 py-2 flex items-center justify-between gap-2">
          <code className="text-xs text-text-primary break-all">{result.raw_key}</code>
          <button
            type="button"
            onClick={handleCopy}
            className="shrink-0 px-3 py-1 text-xs bg-surface-tertiary rounded-lg hover:bg-surface-secondary transition-colors"
          >
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
        <dl className="text-sm text-text-muted space-y-1">
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
            className="px-4 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors"
          >
            Done
          </button>
        </div>
      </motion.div>
    </motion.div>
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
    <PageContainer className="p-6 space-y-4">
      <h2 className="text-lg font-semibold text-text-primary">API Keys</h2>

      {showCreate && <CreateKeyModal onClose={() => setShowCreate(false)} onCreated={handleCreated} />}
      {newKeyResult && <NewKeyDialog result={newKeyResult} onClose={() => setNewKeyResult(null)} />}

      <div className="flex items-center justify-between gap-3">
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            value={principalInput}
            onChange={(e) => setPrincipalInput(e.target.value)}
            placeholder="Filter by principal ID..."
            className="border border-border-strong rounded-lg px-3 py-1.5 text-sm w-64 bg-surface focus:outline-none focus:ring-2 focus:ring-accent"
          />
          <button
            type="submit"
            className="px-3 py-1.5 text-sm bg-surface border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
          >
            Search
          </button>
        </form>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors"
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
        <p className="text-sm text-danger">Failed to load API keys.</p>
      ) : keys.length === 0 ? (
        <EmptyState message={`No API keys found for "${searchPrincipal}".`} />
      ) : (
        <div className="bg-surface rounded-xl border border-border overflow-hidden shadow-xs">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">Name</th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">Key ID</th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">
                  Principal
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">
                  Created
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">
                  Expires
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">Status</th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr
                  key={key.key_id}
                  className="border-b border-border hover:bg-surface-secondary transition-colors duration-150"
                >
                  <td className="py-2 px-3 text-sm text-text-primary">{key.name ?? '-'}</td>
                  <td className="py-2 px-3 text-sm font-mono text-text-muted">{truncate(key.key_id, 16)}</td>
                  <td className="py-2 px-3 text-sm text-text-muted">{key.principal_id}</td>
                  <td className="py-2 px-3 text-sm text-text-muted">{formatDate(key.created_at)}</td>
                  <td className="py-2 px-3 text-sm text-text-muted">{formatDate(key.expires_at)}</td>
                  <td className="py-2 px-3 text-sm">
                    {key.revoked ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-surface-tertiary text-text-muted">
                        Revoked
                      </span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-success-surface text-success-text">
                        Active
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3">
                    <button
                      type="button"
                      disabled={!!key.revoked || revokeMutation.isPending}
                      onClick={() => revokeMutation.mutate(key.key_id)}
                      className="text-xs text-danger hover:underline disabled:opacity-40 disabled:cursor-not-allowed"
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
    </PageContainer>
  )
}

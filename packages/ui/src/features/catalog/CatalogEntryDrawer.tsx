import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, Trash2, Rocket } from 'lucide-react'

import { Drawer } from '@/components/ui/Drawer'
import { catalogApi } from '@/api/catalog'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import type { McpProviderEntry } from '@/types/catalog'

interface CatalogEntryDrawerProps {
  entry: McpProviderEntry | null
  open: boolean
  onClose: () => void
  onDeploy: (entry: McpProviderEntry) => void
}

const MODE_BADGE_STYLES: Record<McpProviderEntry['mode'], string> = {
  subprocess: 'bg-surface-tertiary text-text-secondary',
  docker: 'bg-accent-surface text-accent-text',
  remote: 'bg-warning-surface text-warning-text',
}

export function CatalogEntryDrawer({ entry, open, onClose, onDeploy }: CatalogEntryDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (entryId: string) => catalogApi.remove(entryId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.catalog.all })
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleDelete = () => {
    if (!entry) return
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    deleteMutation.mutate(entry.entry_id)
  }

  const handleOpenChange = (o: boolean) => {
    if (!o) {
      setConfirmDelete(false)
      setError(null)
      onClose()
    }
  }

  if (!entry) {
    return (
      <Drawer open={false} onOpenChange={handleOpenChange} title="">
        <div />
      </Drawer>
    )
  }

  const footer = (
    <>
      <div>
        {!entry.builtin && (
          <button
            type="button"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-danger text-white hover:bg-danger-hover disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Trash2 size={14} />
            {confirmDelete ? 'Confirm Delete' : 'Delete'}
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDeploy(entry)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-accent text-white hover:bg-accent-hover"
      >
        <Rocket size={14} />
        Deploy as Provider
      </button>
    </>
  )

  return (
    <Drawer open={open} onOpenChange={handleOpenChange} title={entry.name} footer={footer}>
      <div className="space-y-5">
        {/* Header badges */}
        <div className="flex items-center gap-2">
          <span className={cn('inline-block rounded px-2 py-0.5 text-xs font-medium', MODE_BADGE_STYLES[entry.mode])}>
            {entry.mode}
          </span>
          {entry.verified && (
            <span className="inline-flex items-center gap-1 text-xs text-success">
              <CheckCircle size={14} />
              Verified
            </span>
          )}
        </div>

        {/* Description */}
        <section>
          <h3 className="text-sm font-medium text-text-secondary mb-1">Description</h3>
          <p className="text-sm text-text-muted">{entry.description}</p>
        </section>

        {/* Connection */}
        <section>
          <h3 className="text-sm font-medium text-text-secondary mb-1">Connection</h3>
          {entry.command.length > 0 && (
            <pre className="bg-surface-tertiary p-2 rounded text-xs font-mono text-text-primary overflow-x-auto">
              {entry.command.join(' ')}
            </pre>
          )}
          {entry.image && (
            <p className="text-sm text-text-muted mt-1">
              Image: <code className="bg-surface-tertiary px-1 py-0.5 rounded text-xs">{entry.image}</code>
            </p>
          )}
          {entry.command.length === 0 && !entry.image && (
            <p className="text-sm text-text-faint">No connection details</p>
          )}
        </section>

        {/* Tags */}
        {entry.tags.length > 0 && (
          <section>
            <h3 className="text-sm font-medium text-text-secondary mb-1">Tags</h3>
            <div className="flex flex-wrap gap-1">
              {entry.tags.map((tag) => (
                <span
                  key={tag}
                  className="inline-block rounded-full bg-surface-tertiary px-2 py-0.5 text-xs text-text-muted"
                >
                  {tag}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Required env vars */}
        {entry.required_env.length > 0 && (
          <section>
            <h3 className="text-sm font-medium text-text-secondary mb-1">Required Environment Variables</h3>
            <div className="flex flex-wrap gap-1">
              {entry.required_env.map((env) => (
                <span
                  key={env}
                  className="inline-block rounded bg-warning-surface border border-warning px-2 py-0.5 text-xs font-mono text-warning-text"
                >
                  {env}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Metadata */}
        <section>
          <h3 className="text-sm font-medium text-text-secondary mb-1">Metadata</h3>
          <dl className="grid grid-cols-2 gap-y-1 text-sm">
            <dt className="text-text-muted">Source</dt>
            <dd className="text-text-primary">{entry.source}</dd>
            <dt className="text-text-muted">Entry ID</dt>
            <dd className="text-text-primary font-mono text-xs">{entry.entry_id}</dd>
          </dl>
        </section>

        {error && <p className="text-sm text-danger">{error}</p>}
      </div>
    </Drawer>
  )
}

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
  subprocess: 'bg-gray-100 text-gray-700',
  docker: 'bg-blue-100 text-blue-700',
  remote: 'bg-purple-100 text-purple-700',
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
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Trash2 size={14} />
            {confirmDelete ? 'Confirm Delete' : 'Delete'}
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDeploy(entry)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700"
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
            <span className="inline-flex items-center gap-1 text-xs text-green-600">
              <CheckCircle size={14} />
              Verified
            </span>
          )}
        </div>

        {/* Description */}
        <section>
          <h3 className="text-sm font-medium text-gray-700 mb-1">Description</h3>
          <p className="text-sm text-gray-600">{entry.description}</p>
        </section>

        {/* Connection */}
        <section>
          <h3 className="text-sm font-medium text-gray-700 mb-1">Connection</h3>
          {entry.command.length > 0 && (
            <pre className="bg-gray-100 p-2 rounded text-xs font-mono text-gray-800 overflow-x-auto">
              {entry.command.join(' ')}
            </pre>
          )}
          {entry.image && (
            <p className="text-sm text-gray-600 mt-1">
              Image: <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">{entry.image}</code>
            </p>
          )}
          {entry.command.length === 0 && !entry.image && <p className="text-sm text-gray-400">No connection details</p>}
        </section>

        {/* Tags */}
        {entry.tags.length > 0 && (
          <section>
            <h3 className="text-sm font-medium text-gray-700 mb-1">Tags</h3>
            <div className="flex flex-wrap gap-1">
              {entry.tags.map((tag) => (
                <span key={tag} className="inline-block rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                  {tag}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Required env vars */}
        {entry.required_env.length > 0 && (
          <section>
            <h3 className="text-sm font-medium text-gray-700 mb-1">Required Environment Variables</h3>
            <div className="flex flex-wrap gap-1">
              {entry.required_env.map((env) => (
                <span
                  key={env}
                  className="inline-block rounded bg-amber-50 border border-amber-200 px-2 py-0.5 text-xs font-mono text-amber-700"
                >
                  {env}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Metadata */}
        <section>
          <h3 className="text-sm font-medium text-gray-700 mb-1">Metadata</h3>
          <dl className="grid grid-cols-2 gap-y-1 text-sm">
            <dt className="text-gray-500">Source</dt>
            <dd className="text-gray-800">{entry.source}</dd>
            <dt className="text-gray-500">Entry ID</dt>
            <dd className="text-gray-800 font-mono text-xs">{entry.entry_id}</dd>
          </dl>
        </section>

        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>
    </Drawer>
  )
}

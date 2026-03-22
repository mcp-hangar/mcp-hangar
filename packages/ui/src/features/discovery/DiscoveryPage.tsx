import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, RefreshCw, Eye, EyeOff } from 'lucide-react'

import { discoveryApi } from '@/api/discovery'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { ActionButton, EmptyState } from '@/components/ui'
import type { DiscoverySourceStatus } from '@/types/system'

import { AddSourceDrawer } from './AddSourceDrawer'
import { EditSourceDrawer } from './EditSourceDrawer'

export function DiscoveryPage(): JSX.Element {
  const queryClient = useQueryClient()
  const [isAddSourceOpen, setIsAddSourceOpen] = useState(false)
  const [editingSource, setEditingSource] = useState<DiscoverySourceStatus | null>(null)

  const { data: sourcesData } = useQuery({
    queryKey: queryKeys.discovery.sources(),
    queryFn: discoveryApi.sources,
    refetchInterval: 30_000,
  })
  const { data: pendingData } = useQuery({
    queryKey: queryKeys.discovery.pending(),
    queryFn: discoveryApi.pending,
    refetchInterval: 15_000,
  })
  const { data: quarantinedData } = useQuery({
    queryKey: queryKeys.discovery.quarantined(),
    queryFn: discoveryApi.quarantined,
    refetchInterval: 30_000,
  })

  const approveMutation = useMutation({
    mutationFn: (name: string) => discoveryApi.approve(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all }),
  })
  const rejectMutation = useMutation({
    mutationFn: (name: string) => discoveryApi.reject(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all }),
  })
  const deregisterMutation = useMutation({
    mutationFn: (sourceId: string) => discoveryApi.deregisterSource(sourceId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all }),
  })
  const scanMutation = useMutation({
    mutationFn: (sourceId: string) => discoveryApi.triggerScan(sourceId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all }),
  })
  const toggleMutation = useMutation({
    mutationFn: ({ sourceId, enabled }: { sourceId: string; enabled: boolean }) =>
      discoveryApi.toggleSource(sourceId, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all }),
  })

  const sources = sourcesData?.sources ?? []
  const pending = pendingData?.pending ?? []
  const quarantined = quarantinedData?.quarantined ?? []

  const handleDelete = (sourceId: string) => {
    if (window.confirm(`Delete discovery source "${sourceId}"?`)) {
      deregisterMutation.mutate(sourceId)
    }
  }

  return (
    <div className="space-y-6 p-6">
      <h2 className="text-lg font-semibold text-gray-900">Discovery</h2>

      {/* Section 1 -- Discovery Sources */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-700">Discovery Sources</h3>
          <button
            type="button"
            onClick={() => setIsAddSourceOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700"
          >
            <Plus size={14} />
            Add Source
          </button>
        </div>
        {sources.length === 0 ? (
          <EmptyState message="No discovery sources configured." />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {sources.map((source) => (
              <div
                key={source.source_id}
                className={cn('bg-white rounded-lg border p-4 space-y-2', !source.is_enabled && 'opacity-50')}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-medium text-sm text-gray-900 truncate">{source.source_id}</span>
                    <span className="text-xs bg-gray-100 text-gray-600 rounded px-1.5 shrink-0">
                      {source.source_type}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={() =>
                        toggleMutation.mutate({
                          sourceId: source.source_id,
                          enabled: !source.is_enabled,
                        })
                      }
                      className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                      title={source.is_enabled ? 'Disable source' : 'Enable source'}
                    >
                      {source.is_enabled ? <Eye size={14} /> : <EyeOff size={14} />}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingSource(source)}
                      className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                      title="Edit source"
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => scanMutation.mutate(source.source_id)}
                      disabled={scanMutation.isPending}
                      className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 disabled:opacity-50"
                      title="Trigger scan"
                    >
                      <RefreshCw size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(source.source_id)}
                      className="p-1 rounded text-gray-400 hover:text-red-600 hover:bg-red-50"
                      title="Delete source"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <span
                    className={cn(
                      'inline-block w-2 h-2 rounded-full',
                      source.is_healthy ? 'bg-green-500' : 'bg-red-500'
                    )}
                  />
                  <span className="text-xs text-gray-500">{source.is_healthy ? 'Healthy' : 'Unhealthy'}</span>
                </div>
                {!source.is_healthy && source.error_message && (
                  <p className="text-xs text-red-600 truncate" title={source.error_message}>
                    {source.error_message}
                  </p>
                )}
                {source.last_discovery && (
                  <p className="text-xs text-gray-400">
                    Last scan: {source.last_discovery.replace('T', ' ').split('.')[0]}
                  </p>
                )}
                <p className="text-xs text-gray-500">{source.providers_count} providers found</p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Section 2 -- Pending Providers */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-semibold text-gray-700">Pending Providers</h3>
          <span className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">{pending.length}</span>
        </div>
        {pending.length === 0 ? (
          <EmptyState message="No pending providers." className="py-6" />
        ) : (
          <div className="bg-white rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Name</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Source</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Discovered At</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Mode</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Command</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {pending.map((p) => (
                  <tr key={p.name}>
                    <td className="px-4 py-2 font-mono text-xs text-gray-900">{p.name}</td>
                    <td className="px-4 py-2 text-xs text-gray-600">{p.source_type}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">
                      {p.discovered_at.replace('T', ' ').split('.')[0]}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-600">{p.mode ?? '\u2014'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-gray-600">
                      {(p.connection_info.command as string[] | undefined)?.[0] ?? '\u2014'}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        <ActionButton
                          variant="primary"
                          onClick={() => approveMutation.mutate(p.name)}
                          isLoading={approveMutation.isPending && approveMutation.variables === p.name}
                        >
                          Approve
                        </ActionButton>
                        <ActionButton
                          variant="danger"
                          onClick={() => rejectMutation.mutate(p.name)}
                          isLoading={rejectMutation.isPending && rejectMutation.variables === p.name}
                        >
                          Reject
                        </ActionButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Section 3 -- Quarantined Providers */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-semibold text-gray-700">Quarantined</h3>
          <span className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">{quarantined.length}</span>
        </div>
        {quarantined.length === 0 ? (
          <EmptyState message="No quarantined providers." className="py-6" />
        ) : (
          <div className="bg-white rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Name</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Source</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Quarantined At</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {quarantined.map((q) => (
                  <tr key={q.name}>
                    <td className="px-4 py-2 font-mono text-xs text-gray-900">{q.name}</td>
                    <td className="px-4 py-2 text-xs text-gray-600">{q.provider.source_type}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">
                      {q.quarantine_time.replace('T', ' ').split('.')[0]}
                    </td>
                    <td className="px-4 py-2 text-xs text-red-600">{q.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <AddSourceDrawer open={isAddSourceOpen} onClose={() => setIsAddSourceOpen(false)} />
      <EditSourceDrawer source={editingSource} open={editingSource !== null} onClose={() => setEditingSource(null)} />
    </div>
  )
}

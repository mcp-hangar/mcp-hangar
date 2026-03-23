import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Plus, Pencil, Trash2, RefreshCw, Eye, EyeOff } from 'lucide-react'

import { discoveryApi } from '@/api/discovery'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { staggerContainer, staggerItem } from '@/lib/animations'
import { ActionButton, EmptyState, PageContainer } from '@/components/ui'
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
    <PageContainer className="space-y-6 p-6">
      <h2 className="text-lg font-semibold text-text-primary">Discovery</h2>

      {/* Section 1 -- Discovery Sources */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-secondary">Discovery Sources</h3>
          <button
            type="button"
            onClick={() => setIsAddSourceOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-accent rounded-lg hover:bg-accent-hover transition-colors"
          >
            <Plus size={14} />
            Add Source
          </button>
        </div>
        {sources.length === 0 ? (
          <EmptyState message="No discovery sources configured." />
        ) : (
          <motion.div
            variants={staggerContainer}
            initial="hidden"
            animate="visible"
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"
          >
            {sources.map((source) => (
              <motion.div
                key={source.source_id}
                variants={staggerItem}
                className={cn(
                  'bg-surface rounded-xl border p-4 space-y-2 shadow-xs',
                  !source.is_enabled && 'opacity-50'
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-medium text-sm text-text-primary truncate">{source.source_id}</span>
                    <span className="text-xs bg-surface-tertiary text-text-muted rounded px-1.5 shrink-0">
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
                      className="p-1 rounded text-text-faint hover:text-text-secondary hover:bg-surface-tertiary"
                      title={source.is_enabled ? 'Disable source' : 'Enable source'}
                    >
                      {source.is_enabled ? <Eye size={14} /> : <EyeOff size={14} />}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingSource(source)}
                      className="p-1 rounded text-text-faint hover:text-text-secondary hover:bg-surface-tertiary"
                      title="Edit source"
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => scanMutation.mutate(source.source_id)}
                      disabled={scanMutation.isPending}
                      className="p-1 rounded text-text-faint hover:text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                      title="Trigger scan"
                    >
                      <RefreshCw size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(source.source_id)}
                      className="p-1 rounded text-text-faint hover:text-danger hover:bg-danger-surface"
                      title="Delete source"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <span
                    className={cn('inline-block w-2 h-2 rounded-full', source.is_healthy ? 'bg-success' : 'bg-danger')}
                  />
                  <span className="text-xs text-text-muted">{source.is_healthy ? 'Healthy' : 'Unhealthy'}</span>
                </div>
                {!source.is_healthy && source.error_message && (
                  <p className="text-xs text-danger truncate" title={source.error_message}>
                    {source.error_message}
                  </p>
                )}
                {source.last_discovery && (
                  <p className="text-xs text-text-faint">
                    Last scan: {source.last_discovery.replace('T', ' ').split('.')[0]}
                  </p>
                )}
                <p className="text-xs text-text-muted">{source.providers_count} providers found</p>
              </motion.div>
            ))}
          </motion.div>
        )}
      </section>

      {/* Section 2 -- Pending Providers */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-semibold text-text-secondary">Pending Providers</h3>
          <span className="text-xs bg-surface-tertiary text-text-muted rounded-full px-2 py-0.5">{pending.length}</span>
        </div>
        {pending.length === 0 ? (
          <EmptyState message="No pending providers." className="py-6" />
        ) : (
          <div className="bg-surface rounded-xl border overflow-hidden shadow-xs">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface-tertiary bg-surface-secondary">
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Name
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Source
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Discovered At
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Mode
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Command
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-secondary">
                {pending.map((p) => (
                  <tr key={p.name} className="hover:bg-surface-secondary transition-colors duration-150">
                    <td className="px-4 py-2 font-mono text-xs text-text-primary">{p.name}</td>
                    <td className="px-4 py-2 text-xs text-text-muted">{p.source_type}</td>
                    <td className="px-4 py-2 text-xs text-text-muted">
                      {p.discovered_at.replace('T', ' ').split('.')[0]}
                    </td>
                    <td className="px-4 py-2 text-xs text-text-muted">{p.mode ?? '\u2014'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-text-muted">
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
          <h3 className="text-sm font-semibold text-text-secondary">Quarantined</h3>
          <span className="text-xs bg-surface-tertiary text-text-muted rounded-full px-2 py-0.5">
            {quarantined.length}
          </span>
        </div>
        {quarantined.length === 0 ? (
          <EmptyState message="No quarantined providers." className="py-6" />
        ) : (
          <div className="bg-surface rounded-xl border overflow-hidden shadow-xs">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface-tertiary bg-surface-secondary">
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Name
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Source
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Quarantined At
                  </th>
                  <th className="text-left text-[11px] font-medium uppercase tracking-wider text-text-muted px-4 py-2.5">
                    Reason
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-secondary">
                {quarantined.map((q) => (
                  <tr key={q.name} className="hover:bg-surface-secondary transition-colors duration-150">
                    <td className="px-4 py-2 font-mono text-xs text-text-primary">{q.name}</td>
                    <td className="px-4 py-2 text-xs text-text-muted">{q.provider.source_type}</td>
                    <td className="px-4 py-2 text-xs text-text-muted">
                      {q.quarantine_time.replace('T', ' ').split('.')[0]}
                    </td>
                    <td className="px-4 py-2 text-xs text-danger">{q.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <AddSourceDrawer open={isAddSourceOpen} onClose={() => setIsAddSourceOpen(false)} />
      <EditSourceDrawer source={editingSource} open={editingSource !== null} onClose={() => setEditingSource(null)} />
    </PageContainer>
  )
}

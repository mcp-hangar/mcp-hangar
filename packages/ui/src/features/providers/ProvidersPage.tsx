import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { queryKeys } from '../../lib/queryKeys'
import { providersApi } from '../../api/providers'
import { EmptyState, LoadingSpinner } from '../../components/ui'
import { ProviderRow } from '../../components/providers/ProviderRow'
import { ProviderCreateDrawer } from './ProviderCreateDrawer'
import { ProviderEditDrawer } from './ProviderEditDrawer'
import { ProviderDeleteDialog } from './ProviderDeleteDialog'
import type { ProviderSummary } from '../../types/provider'

const STATE_FILTERS: Array<{ label: string; value: string | undefined }> = [
  { label: 'All', value: undefined },
  { label: 'Ready', value: 'ready' },
  { label: 'Cold', value: 'cold' },
  { label: 'Degraded', value: 'degraded' },
  { label: 'Dead', value: 'dead' },
]

export function ProvidersPage(): JSX.Element {
  const queryClient = useQueryClient()
  const [stateFilter, setStateFilter] = useState<string | undefined>(undefined)
  const [createOpen, setCreateOpen] = useState(false)
  const [editProvider, setEditProvider] = useState<ProviderSummary | null>(null)
  const [deleteProvider, setDeleteProvider] = useState<ProviderSummary | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.providers.list(stateFilter),
    queryFn: () => providersApi.list(stateFilter),
    refetchInterval: 15_000,
  })

  const { data: editDetails } = useQuery({
    queryKey: queryKeys.providers.detail(editProvider?.provider_id ?? ''),
    queryFn: () => providersApi.get(editProvider!.provider_id),
    enabled: !!editProvider,
  })

  const startMutation = useMutation({
    mutationFn: (id: string) => providersApi.start(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.providers.all }),
  })

  const stopMutation = useMutation({
    mutationFn: (id: string) => providersApi.stop(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.providers.all }),
  })

  const providers = data?.providers ?? []

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Providers</h2>
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          <Plus size={16} />
          New Provider
        </button>
      </div>

      {/* State Filter Tabs */}
      <div className="flex gap-1 mb-4">
        {STATE_FILTERS.map((filter) => (
          <button
            key={filter.label}
            type="button"
            onClick={() => setStateFilter(filter.value)}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              stateFilter === filter.value
                ? 'bg-blue-600 text-white'
                : 'bg-white text-gray-600 border border-gray-300 hover:bg-gray-50'
            }`}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner size={32} />
        </div>
      ) : error ? (
        <p className="text-sm text-red-600">Failed to load providers.</p>
      ) : providers.length === 0 ? (
        <EmptyState message="No providers match the filter." />
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Provider</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">State</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Mode</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3 text-right">Tools</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Health</th>
                <th className="text-xs font-medium text-gray-500 uppercase py-2 px-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((p) => (
                <ProviderRow
                  key={p.provider_id}
                  provider={p}
                  onStart={(id) => startMutation.mutate(id)}
                  onStop={(id) => stopMutation.mutate(id)}
                  isStarting={startMutation.isPending && startMutation.variables === p.provider_id}
                  isStopping={stopMutation.isPending && stopMutation.variables === p.provider_id}
                  onEdit={setEditProvider}
                  onDelete={setDeleteProvider}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ProviderCreateDrawer open={createOpen} onOpenChange={setCreateOpen} />

      {editDetails && (
        <ProviderEditDrawer
          key={editProvider?.provider_id}
          provider={editDetails}
          open={!!editProvider}
          onOpenChange={(o) => {
            if (!o) setEditProvider(null)
          }}
        />
      )}

      {deleteProvider && (
        <ProviderDeleteDialog
          provider={deleteProvider}
          open={!!deleteProvider}
          onOpenChange={(o) => {
            if (!o) setDeleteProvider(null)
          }}
        />
      )}
    </div>
  )
}

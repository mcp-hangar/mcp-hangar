import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { discoveryApi } from '../../api/discovery'
import { queryKeys } from '../../lib/queryKeys'
import { ActionButton, EmptyState } from '../../components/ui'

export function DiscoveryPage(): JSX.Element {
  const queryClient = useQueryClient()

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
    mutationFn: (id: string) => discoveryApi.approve(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all }),
  })
  const rejectMutation = useMutation({
    mutationFn: (id: string) => discoveryApi.reject(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all }),
  })

  const sources = sourcesData?.sources ?? []
  const pending = pendingData?.pending ?? []
  const quarantined = quarantinedData?.quarantined ?? []

  return (
    <div className="space-y-6 p-6">
      <h2 className="text-lg font-semibold text-gray-900">Discovery</h2>

      {/* Section 1 — Discovery Sources */}
      <section>
        <h3 className="text-sm font-semibold text-gray-700 mb-3">Discovery Sources</h3>
        {sources.length === 0 ? (
          <EmptyState message="No discovery sources configured." />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {sources.map((source) => (
              <div key={source.source_id} className="bg-white rounded-lg border p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm text-gray-900 truncate">{source.source_id}</span>
                  <span className="text-xs bg-gray-100 text-gray-600 rounded px-1.5 shrink-0">
                    {source.source_type}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span
                    className={`inline-block w-2 h-2 rounded-full ${source.healthy ? 'bg-green-500' : 'bg-red-500'}`}
                  />
                  <span className="text-xs text-gray-500">{source.healthy ? 'Healthy' : 'Unhealthy'}</span>
                </div>
                {!source.healthy && source.error && (
                  <p className="text-xs text-red-600 truncate" title={source.error}>
                    {source.error}
                  </p>
                )}
                {source.last_scan && (
                  <p className="text-xs text-gray-400">Last scan: {source.last_scan.replace('T', ' ').split('.')[0]}</p>
                )}
                <p className="text-xs text-gray-500">{source.provider_count} providers found</p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Section 2 — Pending Providers */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-semibold text-gray-700">Pending Providers</h3>
          <span className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">
            {pending.length}
          </span>
        </div>
        {pending.length === 0 ? (
          <EmptyState message="No pending providers." className="py-6" />
        ) : (
          <div className="bg-white rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Provider ID</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Source</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Discovered At</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Mode</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Command</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {pending.map((p) => (
                  <tr key={p.provider_id}>
                    <td className="px-4 py-2 font-mono text-xs text-gray-900">{p.provider_id}</td>
                    <td className="px-4 py-2 text-xs text-gray-600">{p.source_id}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">
                      {p.discovered_at.replace('T', ' ').split('.')[0]}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-600">{p.mode ?? '—'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-gray-600">
                      {p.command?.[0] ?? '—'}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        <ActionButton
                          variant="primary"
                          onClick={() => approveMutation.mutate(p.provider_id)}
                          isLoading={approveMutation.isPending && approveMutation.variables === p.provider_id}
                        >
                          Approve
                        </ActionButton>
                        <ActionButton
                          variant="danger"
                          onClick={() => rejectMutation.mutate(p.provider_id)}
                          isLoading={rejectMutation.isPending && rejectMutation.variables === p.provider_id}
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

      {/* Section 3 — Quarantined Providers */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-semibold text-gray-700">Quarantined</h3>
          <span className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">
            {quarantined.length}
          </span>
        </div>
        {quarantined.length === 0 ? (
          <EmptyState message="No quarantined providers." className="py-6" />
        ) : (
          <div className="bg-white rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Provider ID</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Source</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Quarantined At</th>
                  <th className="text-left text-xs font-medium text-gray-500 px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {quarantined.map((q) => (
                  <tr key={q.provider_id}>
                    <td className="px-4 py-2 font-mono text-xs text-gray-900">{q.provider_id}</td>
                    <td className="px-4 py-2 text-xs text-gray-600">{q.source_id}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">
                      {q.quarantined_at.replace('T', ' ').split('.')[0]}
                    </td>
                    <td className="px-4 py-2 text-xs text-red-600">{q.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

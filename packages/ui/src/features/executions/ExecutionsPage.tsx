import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { queryKeys } from '../../lib/queryKeys'
import { providersApi } from '../../api/providers'
import { staggerContainer } from '../../lib/animations'
import { MetricCard, LoadingSpinner, EmptyState, PageContainer } from '../../components/ui'

export function ExecutionsPage(): JSX.Element {
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null)
  const [viewTab, setViewTab] = useState<'all' | 'failures'>('all')

  const { data: providersData } = useQuery({
    queryKey: queryKeys.providers.list(),
    queryFn: () => providersApi.list(),
  })

  const { data: historyData, isLoading } = useQuery({
    queryKey: queryKeys.providers.toolHistory(selectedProviderId ?? ''),
    queryFn: () => providersApi.toolHistory(selectedProviderId!),
    enabled: !!selectedProviderId,
  })

  const history = historyData?.history ?? []
  const displayedHistory = viewTab === 'failures' ? history.filter((r) => r.success === false) : history

  const successCount = history.filter((r) => r.success === true).length
  const successRate = history.length === 0 ? 'N/A' : ((successCount / history.length) * 100).toFixed(1) + '%'
  const durations = history.flatMap((r) => (r.duration_ms != null ? [r.duration_ms] : []))
  const p95 = computeP95(durations)

  return (
    <PageContainer className="p-6 space-y-6">
      <h2 className="text-lg font-semibold text-text-primary">Executions</h2>

      {/* Provider selector */}
      <div className="flex items-center gap-3">
        <label htmlFor="exec-provider-select" className="text-sm font-medium text-text-secondary">
          Provider
        </label>
        <select
          id="exec-provider-select"
          className="text-sm border border-border-strong rounded-lg px-3 py-1.5 bg-surface"
          value={selectedProviderId ?? ''}
          onChange={(e) => {
            setSelectedProviderId(e.target.value || null)
            setViewTab('all')
          }}
        >
          <option value="">Select a provider</option>
          {(providersData?.providers ?? []).map((p) => (
            <option key={p.provider_id} value={p.provider_id}>
              {p.provider_id}
            </option>
          ))}
        </select>
      </div>

      {!selectedProviderId ? (
        <EmptyState message="Select a provider to view execution history." className="py-12" />
      ) : isLoading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner size={32} />
        </div>
      ) : (
        <>
          {/* Summary cards */}
          <motion.div
            variants={staggerContainer}
            initial="hidden"
            animate="visible"
            className="grid grid-cols-2 lg:grid-cols-3 gap-4"
          >
            <MetricCard label="Total Invocations" value={history.length} />
            <MetricCard label="Success Rate" value={successRate} />
            <MetricCard label="p95 Duration" value={p95 > 0 ? `${p95}ms` : 'N/A'} />
          </motion.div>

          {/* Tab controls */}
          <div className="flex gap-2">
            {(['all', 'failures'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setViewTab(tab)}
                className={`text-sm px-4 py-1.5 rounded-lg border font-medium transition-colors ${
                  viewTab === tab
                    ? 'bg-accent text-white border-accent'
                    : 'bg-surface text-text-secondary border-border-strong hover:bg-surface-secondary'
                }`}
              >
                {tab === 'all' ? 'All' : 'Failures'}
                {tab === 'failures' && history.filter((r) => r.success === false).length > 0 && (
                  <span className="ml-1.5 bg-danger-surface text-danger text-xs px-1.5 py-0.5 rounded-full">
                    {history.filter((r) => r.success === false).length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* History table */}
          <div className="bg-surface rounded-xl border border-border shadow-xs">
            {displayedHistory.length === 0 ? (
              <div className="p-4">
                <EmptyState message={viewTab === 'failures' ? 'No failed invocations.' : 'No invocations.'} />
              </div>
            ) : (
              <table className="table-auto w-full text-sm">
                <thead>
                  <tr className="bg-surface-secondary text-left">
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Tool
                    </th>
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Requested At
                    </th>
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Duration
                    </th>
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Status
                    </th>
                    {viewTab === 'failures' && (
                      <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                        Error
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {displayedHistory.map((r) => (
                    <tr
                      key={r.correlation_id}
                      className="border-t border-surface-tertiary hover:bg-surface-secondary transition-colors duration-150"
                    >
                      <td className="px-3 py-2 font-mono text-text-primary">{r.tool_name}</td>
                      <td className="px-3 py-2 text-text-muted text-xs">{new Date(r.requested_at).toLocaleString()}</td>
                      <td className="px-3 py-2 text-text-muted">
                        {r.duration_ms != null ? `${r.duration_ms}ms` : '\u2014'}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                            r.success === true
                              ? 'bg-success-surface text-success-text'
                              : r.success === false
                                ? 'bg-danger-surface text-danger-text'
                                : 'bg-surface-tertiary text-text-muted'
                          }`}
                        >
                          {r.success === true ? 'success' : r.success === false ? 'failed' : 'unknown'}
                        </span>
                      </td>
                      {viewTab === 'failures' && (
                        <td className="px-3 py-2 text-xs text-danger max-w-xs truncate">{r.error ?? '\u2014'}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </PageContainer>
  )
}

function computeP95(durations: number[]): number {
  if (durations.length === 0) return 0
  const sorted = [...durations].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length * 0.95)] ?? 0
}

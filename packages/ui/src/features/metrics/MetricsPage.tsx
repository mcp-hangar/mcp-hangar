import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { queryKeys } from '../../lib/queryKeys'
import { metricsApi } from '../../api/metrics'
import { providersApi } from '../../api/providers'
import { MetricCard, LoadingSpinner, EmptyState } from '../../components/ui'
import type { ProviderMetrics } from '../../types/metrics'

export function MetricsPage(): JSX.Element {
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null)

  const { data: providersData } = useQuery({
    queryKey: queryKeys.providers.list(),
    queryFn: () => providersApi.list(),
  })

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.observability.metrics(),
    queryFn: () => metricsApi.snapshot(),
    refetchInterval: 30_000,
  })

  const providers = data?.providers ?? []
  const selectedMetrics = providers.find((p) => p.provider_id === selectedProviderId) ?? null

  const chartData = [...providers].sort((a, b) => b.tool_call_errors - a.tool_call_errors)

  function computeAvailability(m: ProviderMetrics): string {
    if (m.health_checks_total === 0) return 'N/A'
    const pct = ((m.health_checks_total - m.health_check_failures) / m.health_checks_total) * 100
    return pct.toFixed(1) + '%'
  }

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-lg font-semibold text-gray-900">Metrics</h2>

      {/* Provider selector */}
      <div className="flex items-center gap-3">
        <label htmlFor="provider-select" className="text-sm font-medium text-gray-700">
          Provider
        </label>
        <select
          id="provider-select"
          className="text-sm border border-gray-300 rounded-md px-3 py-1.5 bg-white"
          value={selectedProviderId ?? ''}
          onChange={(e) => setSelectedProviderId(e.target.value || null)}
        >
          <option value="">All providers</option>
          {(providersData?.providers ?? []).map((p) => (
            <option key={p.provider_id} value={p.provider_id}>
              {p.provider_id}
            </option>
          ))}
        </select>
      </div>

      {/* Selected provider MetricCards */}
      {selectedMetrics && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <MetricCard label="Tool Calls" value={selectedMetrics.tool_calls_total} />
          <MetricCard label="Errors" value={selectedMetrics.tool_call_errors} />
          <MetricCard label="Cold Starts" value={selectedMetrics.cold_starts_total} />
          <MetricCard label="Health Failures" value={selectedMetrics.health_check_failures} />
        </div>
      )}

      {/* Error rate chart */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3">Tool Call Errors per Provider</h3>
        {isLoading ? (
          <div className="flex justify-center py-6"><LoadingSpinner /></div>
        ) : chartData.length === 0 ? (
          <EmptyState message="No metrics data available." className="py-6" />
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(120, chartData.length * 36)}>
            <BarChart layout="vertical" data={chartData}>
              <YAxis dataKey="provider_id" type="category" width={120} />
              <XAxis type="number" allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="tool_call_errors" fill="#ef4444" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* SLI availability table */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3">SLI Availability</h3>
        {isLoading ? (
          <div className="flex justify-center py-6"><LoadingSpinner /></div>
        ) : providers.length === 0 ? (
          <EmptyState message="No provider metrics." className="py-4" />
        ) : (
          <table className="table-auto w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium text-gray-500">
                <th className="px-3 py-2">Provider</th>
                <th className="px-3 py-2">Health Checks</th>
                <th className="px-3 py-2">Failures</th>
                <th className="px-3 py-2">Availability</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((m) => (
                <tr key={m.provider_id} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-mono text-gray-800">{m.provider_id}</td>
                  <td className="px-3 py-2 text-gray-600">{m.health_checks_total}</td>
                  <td className="px-3 py-2 text-gray-600">{m.health_check_failures}</td>
                  <td className="px-3 py-2 font-medium text-gray-800">{computeAvailability(m)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

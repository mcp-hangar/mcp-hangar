import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { format } from 'date-fns'
import { queryKeys } from '../../lib/queryKeys'
import { metricsApi } from '../../api/metrics'
import { providersApi } from '../../api/providers'
import { MetricCard, LoadingSpinner, EmptyState } from '../../components/ui'
import type { MetricHistoryPoint, ProviderMetrics } from '../../types/metrics'

type TimeRange = 'live' | '1h' | '6h' | '24h' | '7d'

const TIME_RANGE_OPTIONS: { label: string; value: TimeRange; seconds: number | null }[] = [
  { label: 'Live', value: 'live', seconds: null },
  { label: '1h', value: '1h', seconds: 3600 },
  { label: '6h', value: '6h', seconds: 6 * 3600 },
  { label: '24h', value: '24h', seconds: 24 * 3600 },
  { label: '7d', value: '7d', seconds: 7 * 24 * 3600 },
]

const HISTORY_METRICS = [
  { key: 'tool_calls_total', label: 'Tool Calls' },
  { key: 'tool_call_errors_total', label: 'Errors' },
  { key: 'cold_starts_total', label: 'Cold Starts' },
  { key: 'health_checks_total', label: 'Health Checks' },
]

// Palette for per-provider lines
const LINE_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
  '#8b5cf6', '#06b6d4', '#f97316', '#84cc16',
]

function formatTs(ts: number): string {
  return format(new Date(ts * 1000), 'HH:mm')
}

interface HistoryChartProps {
  points: MetricHistoryPoint[]
  metricKey: string
  metricLabel: string
}

function HistoryChart({ points, metricKey, metricLabel }: HistoryChartProps): JSX.Element {
  const filtered = points.filter((p) => p.metric_name === metricKey)

  // Build sorted list of unique providers and timestamps
  const providerIds = [...new Set(filtered.map((p) => p.provider_id))].sort()
  const timestamps = [...new Set(filtered.map((p) => p.recorded_at))].sort((a, b) => a - b)

  // Pivot: [{recorded_at, [providerId]: value}, ...]
  const byTs = new Map<number, Record<string, number>>()
  for (const p of filtered) {
    if (!byTs.has(p.recorded_at)) byTs.set(p.recorded_at, {})
    byTs.get(p.recorded_at)![p.provider_id] = p.value
  }
  const chartData = timestamps.map((ts) => ({ recorded_at: ts, ...byTs.get(ts) }))

  if (chartData.length === 0) {
    return <EmptyState message="No history data for this metric." className="py-4" />
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-medium text-gray-700 mb-3">{metricLabel} over time</h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData}>
          <XAxis dataKey="recorded_at" tickFormatter={formatTs} tick={{ fontSize: 11 }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
          <Tooltip
            labelFormatter={(v) => format(new Date((v as number) * 1000), 'MMM d HH:mm')}
          />
          <Legend />
          {providerIds.map((pid, i) => (
            <Line
              key={pid}
              type="monotone"
              dataKey={pid}
              name={pid}
              stroke={LINE_COLORS[i % LINE_COLORS.length]}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function MetricsPage(): JSX.Element {
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null)
  const [timeRange, setTimeRange] = useState<TimeRange>('live')

  const { data: providersData } = useQuery({
    queryKey: queryKeys.providers.list(),
    queryFn: () => providersApi.list(),
  })

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.observability.metrics(),
    queryFn: () => metricsApi.snapshot(),
    refetchInterval: 30_000,
    enabled: timeRange === 'live',
  })

  const historyParams = useMemo(() => {
    const rangeOpt = TIME_RANGE_OPTIONS.find((r) => r.value === timeRange)
    if (!rangeOpt || rangeOpt.seconds === null) return null
    const now = Math.floor(Date.now() / 1000)
    return {
      from: now - rangeOpt.seconds,
      to: now,
      ...(selectedProviderId ? { provider: selectedProviderId } : {}),
    }
  }, [timeRange, selectedProviderId])

  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: queryKeys.observability.metricsHistory(historyParams ?? undefined),
    queryFn: () => metricsApi.history(historyParams!),
    enabled: historyParams !== null,
    refetchInterval: 60_000,
  })

  const providers = data?.providers ?? []
  const selectedMetrics = providers.find((p) => p.provider_id === selectedProviderId) ?? null
  const chartData = [...providers].sort((a, b) => b.tool_call_errors - a.tool_call_errors)
  const historyPoints = historyData?.points ?? []

  function computeAvailability(m: ProviderMetrics): string {
    if (m.health_checks_total === 0) return 'N/A'
    const pct = ((m.health_checks_total - m.health_check_failures) / m.health_checks_total) * 100
    return pct.toFixed(1) + '%'
  }

  const isHistoryMode = timeRange !== 'live'

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Metrics</h2>

        {/* Time-range selector */}
        <div className="flex items-center gap-1 bg-gray-100 rounded-md p-1">
          {TIME_RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setTimeRange(opt.value)}
              className={`px-3 py-1 text-sm rounded font-medium transition-colors ${
                timeRange === opt.value
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

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

      {/* Live mode: existing bar chart + SLI table */}
      {!isHistoryMode && (
        <>
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
        </>
      )}

      {/* History mode: time-series line charts */}
      {isHistoryMode && (
        <>
          {historyLoading ? (
            <div className="flex justify-center py-12"><LoadingSpinner /></div>
          ) : historyPoints.length === 0 ? (
            <EmptyState message="No history data for this time range." className="py-12" />
          ) : (
            <div className="space-y-4">
              {HISTORY_METRICS.map((m) => (
                <HistoryChart
                  key={m.key}
                  points={historyPoints}
                  metricKey={m.key}
                  metricLabel={m.label}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

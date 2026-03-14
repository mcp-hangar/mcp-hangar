import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '../../lib/queryKeys'
import { eventsApi } from '../../api/events'
import { LoadingSpinner, EmptyState } from '../../components/ui'
import type { SecurityEvent } from '../../types/metrics'

const SEVERITY_COLOURS: Record<string, string> = {
  low: 'bg-gray-100 text-gray-700',
  medium: 'bg-yellow-100 text-yellow-700',
  high: 'bg-orange-100 text-orange-700',
  critical: 'bg-red-100 text-red-700',
}

export function SecurityPage(): JSX.Element {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.observability.securityEvents(),
    queryFn: () => eventsApi.securityEvents(),
    refetchInterval: 30_000,
  })

  const events: SecurityEvent[] = data?.events ?? []

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Security Events</h2>
        <span className="text-xs text-gray-400">Auto-refreshes every 30s</span>
      </div>

      <div className="bg-white rounded-lg border border-gray-200">
        {isLoading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size={32} />
          </div>
        ) : events.length === 0 ? (
          <div className="p-6">
            <EmptyState message="No security events recorded." />
          </div>
        ) : (
          <table className="table-auto w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium text-gray-500">
                <th className="px-3 py-2">Severity</th>
                <th className="px-3 py-2">Event Type</th>
                <th className="px-3 py-2">Message</th>
                <th className="px-3 py-2">Timestamp</th>
                <th className="px-3 py-2">Provider</th>
                <th className="px-3 py-2">Tool</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event_id} className="border-t border-gray-100">
                  <td className="px-3 py-2">
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        SEVERITY_COLOURS[e.severity] ?? 'bg-gray-100 text-gray-700'
                      }`}
                    >
                      {e.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-gray-800">{e.event_type}</td>
                  <td className="px-3 py-2 text-gray-600 max-w-xs truncate">{e.message}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">
                    {new Date(e.timestamp).toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-gray-600">{e.provider_id ?? '—'}</td>
                  <td className="px-3 py-2 text-gray-600">{e.tool_name ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

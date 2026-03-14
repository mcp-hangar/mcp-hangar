import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '../../lib/queryKeys'
import { systemApi } from '../../api/system'
import { eventsApi } from '../../api/events'
import { MetricCard, LoadingSpinner } from '../../components/ui'
import { StatDistributionChart } from '../../components/dashboard/StatDistributionChart'
import { LiveEventFeed } from '../../components/dashboard/LiveEventFeed'

export function DashboardPage(): JSX.Element {
  const { data: metrics, isLoading: metricsLoading } = useQuery({
    queryKey: queryKeys.system.metrics(),
    queryFn: () => systemApi.metrics(),
    refetchInterval: 30_000,
  })

  const { data: alertsData, isLoading: alertsLoading } = useQuery({
    queryKey: queryKeys.observability.alerts(),
    queryFn: () => eventsApi.alerts(),
    refetchInterval: 60_000,
  })

  const activeAlerts = (alertsData?.alerts ?? []).filter((a) => !a.resolved_at)

  return (
    <div className="p-6 space-y-6">
      {/* Metric Cards Row */}
      {metricsLoading ? (
        <div className="flex justify-center py-8">
          <LoadingSpinner size={32} />
        </div>
      ) : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <MetricCard label="Total Providers" value={metrics?.total_providers ?? '\u2014'} />
          <MetricCard label="Ready" value={metrics?.providers_by_state?.['ready'] ?? '\u2014'} />
          <MetricCard label="Tool Calls" value={metrics?.total_tool_calls ?? '\u2014'} />
          <MetricCard
            label="Error Rate"
            value={
              metrics?.error_rate != null
                ? `${(metrics.error_rate * 100).toFixed(1)}%`
                : '\u2014'
            }
          />
        </div>
      )}

      {/* Chart + Live Feed Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Provider States</h3>
          <StatDistributionChart data={metrics?.providers_by_state ?? {}} />
        </div>
        <LiveEventFeed maxEvents={20} />
      </div>

      {/* Alert Summary */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-medium text-gray-700">Active Alerts</h3>
          <span className="text-xs font-medium bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">
            {activeAlerts.length}
          </span>
        </div>

        {alertsLoading ? (
          <div className="flex justify-center py-4">
            <LoadingSpinner />
          </div>
        ) : activeAlerts.length === 0 ? (
          <p className="text-sm text-gray-400">No active alerts.</p>
        ) : (
          <ul className="space-y-2">
            {activeAlerts.map((alert) => (
              <li key={alert.alert_id} className="flex items-start gap-3">
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${
                    alert.level === 'critical'
                      ? 'bg-red-100 text-red-700'
                      : 'bg-amber-100 text-amber-700'
                  }`}
                >
                  {alert.level}
                </span>
                <span className="text-sm text-gray-700">{alert.message}</span>
                <span className="text-xs text-gray-400 ml-auto shrink-0">
                  {new Date(alert.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

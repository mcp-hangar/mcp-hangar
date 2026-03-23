import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { queryKeys } from '../../lib/queryKeys'
import { systemApi } from '../../api/system'
import { eventsApi } from '../../api/events'
import { staggerContainer, staggerItem } from '../../lib/animations'
import { MetricCard, LoadingSpinner, PageContainer } from '../../components/ui'
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
    <PageContainer className="p-6 space-y-6">
      {/* Metric Cards Row */}
      {metricsLoading ? (
        <div className="flex justify-center py-8">
          <LoadingSpinner size={32} />
        </div>
      ) : (
        <motion.div
          variants={staggerContainer}
          initial="hidden"
          animate="visible"
          className="grid grid-cols-2 lg:grid-cols-4 gap-4"
        >
          <MetricCard label="Total Providers" value={metrics?.total_providers ?? '\u2014'} />
          <MetricCard label="Ready" value={metrics?.providers_by_state?.['ready'] ?? '\u2014'} />
          <MetricCard label="Tool Calls" value={metrics?.total_tool_calls ?? '\u2014'} />
          <MetricCard
            label="Error Rate"
            value={metrics?.error_rate != null ? `${(metrics.error_rate * 100).toFixed(1)}%` : '\u2014'}
          />
        </motion.div>
      )}

      {/* Chart + Live Feed Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <motion.div variants={staggerItem} className="bg-surface rounded-xl border border-border p-4 shadow-xs">
          <h3 className="text-sm font-medium text-text-secondary mb-3">Provider States</h3>
          <StatDistributionChart data={metrics?.providers_by_state ?? {}} />
        </motion.div>
        <LiveEventFeed maxEvents={20} />
      </div>

      {/* Alert Summary */}
      <div className="bg-surface rounded-xl border border-border p-4 shadow-xs">
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-medium text-text-secondary">Active Alerts</h3>
          <span className="text-xs font-medium bg-surface-tertiary text-text-muted rounded-full px-2 py-0.5">
            {activeAlerts.length}
          </span>
        </div>

        {alertsLoading ? (
          <div className="flex justify-center py-4">
            <LoadingSpinner />
          </div>
        ) : activeAlerts.length === 0 ? (
          <p className="text-sm text-text-faint">No active alerts.</p>
        ) : (
          <ul className="space-y-2">
            {activeAlerts.map((alert) => (
              <li key={alert.alert_id} className="flex items-start gap-3">
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${
                    alert.level === 'critical'
                      ? 'bg-danger-surface text-danger-text'
                      : 'bg-warning-surface text-warning-text'
                  }`}
                >
                  {alert.level}
                </span>
                <span className="text-sm text-text-secondary">{alert.message}</span>
                <span className="text-xs text-text-faint ml-auto shrink-0">
                  {new Date(alert.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PageContainer>
  )
}

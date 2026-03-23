import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '../../lib/queryKeys'
import { eventsApi } from '../../api/events'
import { LoadingSpinner, EmptyState, PageContainer } from '../../components/ui'
import { RolesTab } from './RolesTab'
import { PrincipalsTab } from './PrincipalsTab'
import type { SecurityEvent } from '../../types/metrics'

// ---- Security Events sub-tab ------------------------------------------------

const SEVERITY_COLOURS: Record<string, string> = {
  low: 'bg-surface-tertiary text-text-secondary',
  medium: 'bg-warning-surface text-warning-text',
  high: 'bg-warning-surface text-warning-text',
  critical: 'bg-danger-surface text-danger-text',
}

function EventsTab(): JSX.Element {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.observability.securityEvents(),
    queryFn: () => eventsApi.securityEvents(),
    refetchInterval: 30_000,
  })

  const events: SecurityEvent[] = data?.events ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <span className="text-xs text-text-faint">Auto-refreshes every 30s</span>
      </div>

      <div className="bg-surface rounded-xl border border-border shadow-xs">
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
              <tr className="bg-surface-secondary text-left">
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                  Severity
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                  Event Type
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                  Message
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                  Timestamp
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                  Provider
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">Tool</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr
                  key={e.event_id}
                  className="border-t border-surface-tertiary hover:bg-surface-secondary transition-colors duration-150"
                >
                  <td className="px-3 py-2">
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        SEVERITY_COLOURS[e.severity] ?? 'bg-surface-tertiary text-text-secondary'
                      }`}
                    >
                      {e.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-text-primary">{e.event_type}</td>
                  <td className="px-3 py-2 text-text-muted max-w-xs truncate">{e.message}</td>
                  <td className="px-3 py-2 text-xs text-text-muted">{new Date(e.timestamp).toLocaleString()}</td>
                  <td className="px-3 py-2 text-text-muted">{e.provider_id ?? '\u2014'}</td>
                  <td className="px-3 py-2 text-text-muted">{e.tool_name ?? '\u2014'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ---- SecurityPage with tabs -------------------------------------------------

type Tab = 'events' | 'roles' | 'principals'

const TAB_LABELS: Record<Tab, string> = {
  events: 'Events',
  roles: 'Roles',
  principals: 'Principals',
}

export function SecurityPage(): JSX.Element {
  const [tab, setTab] = useState<Tab>('events')

  return (
    <PageContainer className="p-6 space-y-4">
      <h2 className="text-lg font-semibold text-text-primary">Security</h2>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-border pb-0">
        {(['events', 'roles', 'principals'] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t ? 'border-accent text-accent' : 'border-transparent text-text-muted hover:text-text-secondary'
            }`}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {tab === 'events' && <EventsTab />}
      {tab === 'roles' && <RolesTab />}
      {tab === 'principals' && <PrincipalsTab />}
    </PageContainer>
  )
}

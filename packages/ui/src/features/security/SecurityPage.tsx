import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '../../lib/queryKeys'
import { eventsApi } from '../../api/events'
import { LoadingSpinner, EmptyState } from '../../components/ui'
import { RolesTab } from './RolesTab'
import { PrincipalsTab } from './PrincipalsTab'
import type { SecurityEvent } from '../../types/metrics'

// ---- Security Events sub-tab ------------------------------------------------

const SEVERITY_COLOURS: Record<string, string> = {
  low: 'bg-gray-100 text-gray-700',
  medium: 'bg-yellow-100 text-yellow-700',
  high: 'bg-orange-100 text-orange-700',
  critical: 'bg-red-100 text-red-700',
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
                  <td className="px-3 py-2 text-xs text-gray-500">{new Date(e.timestamp).toLocaleString()}</td>
                  <td className="px-3 py-2 text-gray-600">{e.provider_id ?? '\u2014'}</td>
                  <td className="px-3 py-2 text-gray-600">{e.tool_name ?? '\u2014'}</td>
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
    <div className="p-6 space-y-4">
      <h2 className="text-lg font-semibold text-gray-900">Security</h2>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200 pb-0">
        {(['events', 'roles', 'principals'] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {tab === 'events' && <EventsTab />}
      {tab === 'roles' && <RolesTab />}
      {tab === 'principals' && <PrincipalsTab />}
    </div>
  )
}

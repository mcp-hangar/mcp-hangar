import { useState, useEffect } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { queryKeys } from '../../lib/queryKeys'
import { eventsApi } from '../../api/events'
import { LiveEventFeed } from '../../components/dashboard/LiveEventFeed'
import { LoadingSpinner, EmptyState } from '../../components/ui'

const PAGE_SIZE = 20

export function EventsPage(): JSX.Element {
  const [typeFilter, setTypeFilter] = useState<string>('')
  const [providerIdFilter, setProviderIdFilter] = useState<string>('')
  const [eventTypeFilter, setEventTypeFilter] = useState<string>('')
  const [page, setPage] = useState<number>(1)

  useEffect(() => {
    setPage(1)
  }, [providerIdFilter, eventTypeFilter])

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.observability.audit({
      provider_id: providerIdFilter || undefined,
      event_type: eventTypeFilter || undefined,
      limit: 500,
    }),
    queryFn: () =>
      eventsApi.audit({
        provider_id: providerIdFilter || undefined,
        event_type: eventTypeFilter || undefined,
        limit: 500,
      }),
    placeholderData: keepPreviousData,
  })

  const records = data?.records ?? []
  const pageCount = Math.max(1, Math.ceil(records.length / PAGE_SIZE))
  const pageRecords = records.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-lg font-semibold text-gray-900">Events &amp; Logs</h2>

      {/* Live Event Stream */}
      <section className="space-y-3">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-gray-700">Live Stream</h3>
          <select
            className="text-sm border border-gray-300 rounded-md px-3 py-1.5 bg-white"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">All types</option>
            <option value="ProviderStateChanged">ProviderStateChanged</option>
            <option value="ToolInvocationCompleted">ToolInvocationCompleted</option>
            <option value="ToolInvocationFailed">ToolInvocationFailed</option>
            <option value="HealthCheckPassed">HealthCheckPassed</option>
            <option value="HealthCheckFailed">HealthCheckFailed</option>
            <option value="ProviderStarted">ProviderStarted</option>
            <option value="ProviderStopped">ProviderStopped</option>
            <option value="ProviderDegraded">ProviderDegraded</option>
          </select>
        </div>
        <LiveEventFeed typeFilter={typeFilter || undefined} maxEvents={50} />
      </section>

      {/* Audit Log */}
      <section className="space-y-3">
        <h3 className="text-sm font-medium text-gray-700">Audit Log</h3>
        <div className="flex items-center gap-3 flex-wrap">
          <input
            type="text"
            placeholder="Provider ID"
            className="text-sm border border-gray-300 rounded-md px-3 py-1.5"
            value={providerIdFilter}
            onChange={(e) => setProviderIdFilter(e.target.value)}
          />
          <input
            type="text"
            placeholder="Event type"
            className="text-sm border border-gray-300 rounded-md px-3 py-1.5"
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
          />
        </div>

        <div className="bg-white rounded-lg border border-gray-200">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <LoadingSpinner />
            </div>
          ) : records.length === 0 ? (
            <div className="p-4">
              <EmptyState message="No audit records found." />
            </div>
          ) : (
            <>
              <table className="table-auto w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-left text-xs font-medium text-gray-500">
                    <th className="px-3 py-2">Event Type</th>
                    <th className="px-3 py-2">Provider</th>
                    <th className="px-3 py-2">Occurred At</th>
                    <th className="px-3 py-2">Event ID</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRecords.map((r) => (
                    <tr key={r.event_id} className="border-t border-gray-100">
                      <td className="px-3 py-2 font-mono text-gray-800">{r.event_type}</td>
                      <td className="px-3 py-2 text-gray-600">{r.provider_id ?? '—'}</td>
                      <td className="px-3 py-2 text-gray-500">
                        {new Date(r.occurred_at).toLocaleString()}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-400">
                        {r.event_id.slice(0, 8)}...
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flex items-center justify-between px-3 py-2 border-t border-gray-100">
                <span className="text-xs text-gray-500">
                  Page {page} of {pageCount} ({records.length} total)
                </span>
                <div className="flex items-center gap-2">
                  <button
                    className="text-sm px-3 py-1 border border-gray-300 rounded disabled:opacity-40"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                  >
                    Previous
                  </button>
                  <button
                    className="text-sm px-3 py-1 border border-gray-300 rounded disabled:opacity-40"
                    onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                    disabled={page >= pageCount}
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  )
}

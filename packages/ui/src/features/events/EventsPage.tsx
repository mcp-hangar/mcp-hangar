import { useState, useEffect } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { queryKeys } from '../../lib/queryKeys'
import { eventsApi } from '../../api/events'
import { LiveEventFeed } from '../../components/dashboard/LiveEventFeed'
import { LoadingSpinner, EmptyState, PageContainer } from '../../components/ui'

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
    <PageContainer className="p-6 space-y-6">
      <h2 className="text-lg font-semibold text-text-primary">Events &amp; Logs</h2>

      {/* Live Event Stream */}
      <section className="space-y-3">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-text-secondary">Live Stream</h3>
          <select
            className="text-sm border border-border-strong rounded-lg px-3 py-1.5 bg-surface"
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
        <h3 className="text-sm font-medium text-text-secondary">Audit Log</h3>
        <div className="flex items-center gap-3 flex-wrap">
          <input
            type="text"
            placeholder="Provider ID"
            className="text-sm border border-border-strong rounded-lg px-3 py-1.5 bg-surface"
            value={providerIdFilter}
            onChange={(e) => setProviderIdFilter(e.target.value)}
          />
          <input
            type="text"
            placeholder="Event type"
            className="text-sm border border-border-strong rounded-lg px-3 py-1.5 bg-surface"
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
          />
        </div>

        <div className="bg-surface rounded-xl border border-border shadow-xs">
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
                  <tr className="bg-surface-secondary text-left">
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Event Type
                    </th>
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Provider
                    </th>
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Occurred At
                    </th>
                    <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider px-3 py-2.5">
                      Event ID
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pageRecords.map((r) => (
                    <tr
                      key={r.event_id}
                      className="border-t border-surface-tertiary hover:bg-surface-secondary transition-colors duration-150"
                    >
                      <td className="px-3 py-2 font-mono text-text-primary">{r.event_type}</td>
                      <td className="px-3 py-2 text-text-muted">{r.provider_id ?? '\u2014'}</td>
                      <td className="px-3 py-2 text-text-muted">{new Date(r.occurred_at).toLocaleString()}</td>
                      <td className="px-3 py-2 font-mono text-xs text-text-faint">{r.event_id.slice(0, 8)}...</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flex items-center justify-between px-3 py-2.5 border-t border-surface-tertiary">
                <span className="text-xs text-text-muted">
                  Page {page} of {pageCount} ({records.length} total)
                </span>
                <div className="flex items-center gap-2">
                  <button
                    className="text-sm px-3 py-1 border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors disabled:opacity-40"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                  >
                    Previous
                  </button>
                  <button
                    className="text-sm px-3 py-1 border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors disabled:opacity-40"
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
    </PageContainer>
  )
}

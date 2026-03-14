import { useState } from 'react'
import { useParams } from 'react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '../../lib/queryKeys'
import { providersApi } from '../../api/providers'
import {
  ProviderStateBadge,
  HealthBadge,
  CircuitBreakerBadge,
  ActionButton,
  EmptyState,
  LoadingSpinner,
} from '../../components/ui'
import { ToolList } from '../../components/providers/ToolList'
import { useProviderLogs } from '../../hooks/useProviderLogs'
import { LogViewer } from './LogViewer'

export function ProviderDetailPage(): JSX.Element {
  const { id } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const [logsOpen, setLogsOpen] = useState(false)

  const { data: provider, isLoading } = useQuery({
    queryKey: queryKeys.providers.detail(id!),
    queryFn: () => providersApi.get(id!),
    enabled: !!id,
  })

  const { data: healthData } = useQuery({
    queryKey: queryKeys.providers.health(id!),
    queryFn: () => providersApi.health(id!),
    enabled: !!id,
    refetchInterval: 10_000,
  })

  const startMutation = useMutation({
    mutationFn: () => providersApi.start(id!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.providers.all }),
  })

  const stopMutation = useMutation({
    mutationFn: () => providersApi.stop(id!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.providers.all }),
  })

  const { logs, status: logsStatus, clearLogs } = useProviderLogs({
    providerId: id!,
    enabled: !!id && logsOpen,
  })

  if (isLoading) {
    return (
      <div className="flex justify-center p-12">
        <LoadingSpinner size={32} />
      </div>
    )
  }

  if (!provider) {
    return <EmptyState message={`Provider '${id}' not found.`} />
  }

  const health = healthData?.status ?? provider.health
  const cb = provider.circuit_breaker

  return (
    <div className="space-y-6 p-6">
      {/* Header Row */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-xl font-semibold text-gray-900">{provider.provider_id}</h2>
        <ProviderStateBadge state={provider.state} />
        <div className="flex gap-2 ml-auto">
          <ActionButton
            variant="primary"
            disabled={provider.state === 'ready' || provider.state === 'initializing'}
            isLoading={startMutation.isPending}
            onClick={() => startMutation.mutate()}
          >
            Start
          </ActionButton>
          <ActionButton
            variant="danger"
            disabled={provider.state === 'cold' || provider.state === 'dead'}
            isLoading={stopMutation.isPending}
            onClick={() => stopMutation.mutate()}
          >
            Stop
          </ActionButton>
        </div>
      </div>

      {/* Details Card */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 grid grid-cols-2 gap-x-8 gap-y-2">
        <div>
          <span className="text-xs text-gray-500 uppercase font-medium">Mode</span>
          <p className="text-sm text-gray-800 mt-0.5 capitalize">{provider.mode}</p>
        </div>
        <div>
          <span className="text-xs text-gray-500 uppercase font-medium">Idle TTL</span>
          <p className="text-sm text-gray-800 mt-0.5">
            {provider.idle_ttl_s != null ? `${provider.idle_ttl_s}s` : '\u2014'}
          </p>
        </div>
        {provider.command && (
          <div className="col-span-2">
            <span className="text-xs text-gray-500 uppercase font-medium">Command</span>
            <p className="text-sm font-mono text-gray-800 mt-0.5 break-all">
              {provider.command.join(' ')}
            </p>
          </div>
        )}
        <div>
          <span className="text-xs text-gray-500 uppercase font-medium">Health</span>
          <div className="flex items-center gap-2 mt-0.5">
            {health ? (
              <>
                <HealthBadge status={health.status} />
                <span className="text-xs text-gray-400">
                  {health.consecutive_failures} consecutive failures
                </span>
              </>
            ) : (
              <span className="text-sm text-gray-400">\u2014</span>
            )}
          </div>
        </div>
        <div>
          <span className="text-xs text-gray-500 uppercase font-medium">Circuit Breaker</span>
          <div className="flex items-center gap-2 mt-0.5">
            {cb ? (
              <>
                <CircuitBreakerBadge state={cb.state} />
                <span className="text-xs text-gray-400">{cb.failure_count} failures</span>
                {cb.state === 'open' && cb.opened_at && (
                  <span className="text-xs text-gray-400">
                    opened {new Date(cb.opened_at).toLocaleString()}
                  </span>
                )}
              </>
            ) : (
              <span className="text-sm text-gray-400">\u2014</span>
            )}
          </div>
        </div>
      </div>

      {/* Tools Section */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-medium text-gray-700">Tools</h3>
          <span className="text-xs font-medium bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">
            {provider.tools?.length ?? 0}
          </span>
        </div>
        <ToolList tools={provider.tools ?? []} />
      </div>

      {/* Process Logs Section */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <button
          type="button"
          className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-50 transition-colors"
          onClick={() => setLogsOpen((v) => !v)}
          aria-expanded={logsOpen}
        >
          <h3 className="text-sm font-medium text-gray-700">Process Logs</h3>
          <svg
            className={`w-4 h-4 text-gray-400 transition-transform ${logsOpen ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        {logsOpen && (
          <LogViewer logs={logs} status={logsStatus} onClear={clearLogs} />
        )}
      </div>
    </div>
  )
}

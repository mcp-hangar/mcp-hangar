import { useState, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { configApi } from '../../api/config'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'
import { ActionButton, LoadingSpinner } from '../../components/ui'

export function ConfigPage(): JSX.Element {
  const [reloadMessage, setReloadMessage] = useState<{ text: string; ok: boolean } | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.config.current(),
    queryFn: configApi.current,
  })

  const reloadMutation = useMutation({
    mutationFn: configApi.reload,
    onSuccess: (result) => {
      setReloadMessage({ text: result.message ?? 'Config reloaded.', ok: true })
    },
    onError: () => {
      setReloadMessage({ text: 'Reload failed.', ok: false })
    },
  })

  useEffect(() => {
    if (reloadMessage) {
      const t = setTimeout(() => setReloadMessage(null), 5000)
      return () => clearTimeout(t)
    }
  }, [reloadMessage])

  return (
    <div className="space-y-4 p-6">
      {/* Header row */}
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold text-gray-900">Configuration</h2>
        <ActionButton
          variant="primary"
          onClick={() => reloadMutation.mutate()}
          isLoading={reloadMutation.isPending}
        >
          Hot Reload
        </ActionButton>
      </div>

      {/* Inline feedback message */}
      {reloadMessage && (
        <p className={cn('text-sm', reloadMessage.ok ? 'text-green-600' : 'text-red-600')}>
          {reloadMessage.text}
        </p>
      )}

      {/* Config JSON viewer */}
      <div className="bg-white rounded-lg border p-4">
        {isLoading ? (
          <LoadingSpinner />
        ) : error ? (
          <p className="text-sm text-red-600">Failed to load configuration.</p>
        ) : (
          <pre className="text-xs font-mono overflow-x-auto whitespace-pre-wrap text-gray-700">
            {JSON.stringify(data?.config ?? {}, null, 2)}
          </pre>
        )}
      </div>

      <p className="text-xs text-gray-400">
        Configuration is read-only in the UI. Edit the config file and use Hot Reload to apply changes.
      </p>
    </div>
  )
}

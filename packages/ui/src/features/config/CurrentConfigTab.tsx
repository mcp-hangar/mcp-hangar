import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { configApi } from '../../api/config'
import { queryKeys } from '../../lib/queryKeys'
import { ActionButton, LoadingSpinner } from '../../components/ui'

export function CurrentConfigTab(): JSX.Element {
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.config.current(),
    queryFn: configApi.current,
  })

  const reloadMutation = useMutation({
    mutationFn: configApi.reload,
    onSuccess: (result) => {
      toast.success(result.message ?? 'Config reloaded.')
      queryClient.invalidateQueries({ queryKey: queryKeys.config.all })
    },
    onError: () => {
      toast.error('Reload failed.')
    },
  })

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-gray-500">
          Configuration is read-only in the UI. Edit the config file and use Hot Reload to apply changes.
        </p>
        <ActionButton variant="primary" onClick={() => reloadMutation.mutate()} isLoading={reloadMutation.isPending}>
          Hot Reload
        </ActionButton>
      </div>

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
    </div>
  )
}

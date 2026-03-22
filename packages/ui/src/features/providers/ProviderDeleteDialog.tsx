import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { providersApi } from '@/api/providers'
import { queryKeys } from '@/lib/queryKeys'
import type { ProviderSummary } from '@/types/provider'

interface ProviderDeleteDialogProps {
  provider: ProviderSummary
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ProviderDeleteDialog({ provider, open, onOpenChange }: ProviderDeleteDialogProps): JSX.Element {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const isRunning = provider.state === 'ready' || provider.state === 'degraded'
  const isInitializing = provider.state === 'initializing'

  const deleteMutation = useMutation({
    mutationFn: () => providersApi.delete(provider.provider_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all })
      onOpenChange(false)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) setError(null)
        onOpenChange(o)
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 z-40" />
        <Dialog.Content className="fixed inset-0 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
            <Dialog.Title className="text-base font-semibold text-gray-900">Delete Provider</Dialog.Title>
            <p className="text-sm text-gray-600">
              Delete <span className="font-medium">{provider.provider_id}</span>? This cannot be undone.
            </p>
            {isRunning && (
              <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                This provider is running and will be stopped before deletion.
              </p>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <Dialog.Close asChild>
                <button
                  type="button"
                  className="px-4 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
                >
                  Cancel
                </button>
              </Dialog.Close>
              <button
                type="button"
                disabled={isInitializing || deleteMutation.isPending}
                title={isInitializing ? 'Cannot delete a provider that is initializing' : undefined}
                onClick={() => {
                  setError(null)
                  deleteMutation.mutate()
                }}
                className="px-4 py-1.5 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

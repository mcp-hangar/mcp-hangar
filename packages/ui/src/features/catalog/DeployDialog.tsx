import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { catalogApi } from '@/api/catalog'
import { queryKeys } from '@/lib/queryKeys'
import type { McpProviderEntry } from '@/types/catalog'

interface DeployDialogProps {
  entry: McpProviderEntry | null
  open: boolean
  onClose: () => void
}

export function DeployDialog({ entry, open, onClose }: DeployDialogProps): JSX.Element {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const deployMutation = useMutation({
    mutationFn: (entryId: string) => catalogApi.deploy(entryId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.catalog.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all })
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleOpenChange = (o: boolean) => {
    if (!o) {
      setError(null)
      onClose()
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 z-40" />
        <Dialog.Content className="fixed inset-0 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
            <Dialog.Title className="text-base font-semibold text-gray-900">Deploy Provider</Dialog.Title>

            {entry && (
              <>
                <p className="text-sm text-gray-600">
                  Deploy <span className="font-medium">{entry.name}</span> as a new provider? This will create a{' '}
                  {entry.mode} provider using the catalog configuration.
                </p>

                {entry.required_env.length > 0 && (
                  <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                    <p className="font-medium mb-1">Required environment variables:</p>
                    <ul className="list-disc list-inside">
                      {entry.required_env.map((env) => (
                        <li key={env} className="font-mono text-xs">
                          {env}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
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
                disabled={!entry || deployMutation.isPending}
                onClick={() => {
                  if (!entry) return
                  setError(null)
                  deployMutation.mutate(entry.entry_id)
                }}
                className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {deployMutation.isPending ? 'Deploying...' : 'Deploy'}
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { providersApi } from '@/api/providers'
import { queryKeys } from '@/lib/queryKeys'
import { modalVariants, overlayVariants } from '@/lib/animations'
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
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                className="fixed inset-0 bg-overlay z-40"
                variants={overlayVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
              />
            </Dialog.Overlay>
            <Dialog.Content asChild>
              <motion.div
                className="fixed inset-0 flex items-center justify-center z-50 p-4"
                variants={modalVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
              >
                <div className="bg-surface rounded-xl shadow-lg w-full max-w-md p-6 space-y-4">
                  <Dialog.Title className="text-base font-semibold text-text-primary">Delete Provider</Dialog.Title>
                  <p className="text-sm text-text-muted">
                    Delete <span className="font-medium">{provider.provider_id}</span>? This cannot be undone.
                  </p>
                  {isRunning && (
                    <p className="text-sm text-warning-text bg-warning-surface border border-warning rounded-lg px-3 py-2">
                      This provider is running and will be stopped before deletion.
                    </p>
                  )}
                  {error && <p className="text-sm text-danger">{error}</p>}
                  <div className="flex justify-end gap-2 pt-2">
                    <Dialog.Close asChild>
                      <button
                        type="button"
                        className="px-4 py-1.5 text-sm border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
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
                      className="px-4 py-1.5 text-sm bg-danger text-white rounded-lg hover:bg-danger-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>
                </div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}

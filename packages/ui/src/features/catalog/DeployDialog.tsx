import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'

import { catalogApi } from '@/api/catalog'
import { queryKeys } from '@/lib/queryKeys'
import { modalVariants, overlayVariants } from '@/lib/animations'
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
                  <Dialog.Title className="text-base font-semibold text-text-primary">Deploy Provider</Dialog.Title>

                  {entry && (
                    <>
                      <p className="text-sm text-text-muted">
                        Deploy <span className="font-medium">{entry.name}</span> as a new provider? This will create a{' '}
                        {entry.mode} provider using the catalog configuration.
                      </p>

                      {entry.required_env.length > 0 && (
                        <div className="text-sm text-warning-text bg-warning-surface border border-warning rounded-lg px-3 py-2">
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
                      disabled={!entry || deployMutation.isPending}
                      onClick={() => {
                        if (!entry) return
                        setError(null)
                        deployMutation.mutate(entry.entry_id)
                      }}
                      className="px-4 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {deployMutation.isPending ? 'Deploying...' : 'Deploy'}
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

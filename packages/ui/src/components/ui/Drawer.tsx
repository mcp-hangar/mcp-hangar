import * as Dialog from '@radix-ui/react-dialog'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { drawerVariants, overlayVariants } from '@/lib/animations'

interface DrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  width?: 'sm' | 'lg'
  footer?: React.ReactNode
  children: React.ReactNode
}

export function Drawer({ open, onOpenChange, title, width = 'sm', footer, children }: DrawerProps): JSX.Element {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                variants={overlayVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
                className="fixed inset-0 bg-overlay backdrop-blur-[2px] z-40"
              />
            </Dialog.Overlay>
            <Dialog.Content asChild>
              <motion.div
                variants={drawerVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
                className={cn(
                  'fixed inset-y-0 right-0 z-50 flex flex-col bg-surface shadow-xl border-l border-border',
                  'focus:outline-none',
                  width === 'lg' ? 'w-[600px]' : 'w-[480px]'
                )}
              >
                <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
                  <Dialog.Title className="text-sm font-semibold text-text-primary">{title}</Dialog.Title>
                  <Dialog.Close asChild>
                    <button
                      type="button"
                      className="rounded-lg p-1.5 text-text-faint hover:text-text-secondary hover:bg-surface-secondary transition-colors duration-150"
                    >
                      <X size={16} />
                    </button>
                  </Dialog.Close>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
                {footer && (
                  <div className="shrink-0 px-6 py-4 border-t border-border flex justify-between gap-2 bg-surface-secondary/50">
                    {footer}
                  </div>
                )}
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}

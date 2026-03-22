import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'

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
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 z-40" />
        <Dialog.Content
          className={cn(
            'fixed inset-y-0 right-0 z-50 flex flex-col bg-white shadow-2xl',
            'focus:outline-none',
            width === 'lg' ? 'w-[600px]' : 'w-[480px]'
          )}
        >
          <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 shrink-0">
            <Dialog.Title className="text-base font-semibold text-gray-900">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" className="rounded p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100">
                <X size={18} />
              </button>
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
          {footer && (
            <div className="shrink-0 px-6 py-4 border-t border-gray-200 flex justify-between gap-2">{footer}</div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

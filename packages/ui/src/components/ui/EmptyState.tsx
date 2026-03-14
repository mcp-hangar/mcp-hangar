import { Inbox } from 'lucide-react'
import { cn } from '../../lib/cn'

interface EmptyStateProps {
  message?: string
  className?: string
}

export function EmptyState({ message = 'No items found.', className }: EmptyStateProps): JSX.Element {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      <Inbox size={32} className="text-gray-300" />
      <p className="text-sm text-gray-400 mt-2">{message}</p>
    </div>
  )
}

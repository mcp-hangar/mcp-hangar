import { motion } from 'framer-motion'
import { Inbox } from 'lucide-react'
import { cn } from '../../lib/cn'

interface EmptyStateProps {
  message?: string
  className?: string
}

export function EmptyState({ message = 'No items found.', className }: EmptyStateProps): JSX.Element {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: 0.1 }}
      className={cn('flex flex-col items-center justify-center py-16 text-center', className)}
    >
      <div className="rounded-xl bg-surface-secondary p-3 mb-3">
        <Inbox size={24} className="text-text-faint" />
      </div>
      <p className="text-sm text-text-muted">{message}</p>
    </motion.div>
  )
}

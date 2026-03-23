import { motion } from 'framer-motion'
import { cn } from '../../lib/cn'
import { staggerItem } from '../../lib/animations'

interface MetricCardProps {
  label: string
  value: string | number
  subLabel?: string
  className?: string
}

export function MetricCard({ label, value, subLabel, className }: MetricCardProps): JSX.Element {
  return (
    <motion.div
      variants={staggerItem}
      className={cn(
        'bg-surface rounded-xl border border-border p-5 shadow-xs',
        'transition-shadow duration-200 hover:shadow-md',
        className
      )}
    >
      <p className="text-xs font-medium uppercase tracking-wider text-text-faint">{label}</p>
      <p className="text-2xl font-semibold text-text-primary mt-1.5 tabular-nums">{value}</p>
      {subLabel && <p className="text-xs text-text-muted mt-1">{subLabel}</p>}
    </motion.div>
  )
}

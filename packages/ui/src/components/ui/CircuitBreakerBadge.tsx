import type { CircuitBreakerStatus } from '../../types/provider'
import { cn } from '../../lib/cn'

interface CircuitBreakerBadgeProps {
  state: CircuitBreakerStatus['state']
  className?: string
}

const STATE_STYLES: Record<CircuitBreakerStatus['state'], string> = {
  closed: 'bg-success-surface text-success-text border-success/20',
  open: 'bg-danger-surface text-danger-text border-danger/20',
  half_open: 'bg-warning-surface text-warning-text border-warning/20',
}

const STATE_DOT: Record<CircuitBreakerStatus['state'], string> = {
  closed: 'bg-success',
  open: 'bg-danger',
  half_open: 'bg-warning animate-pulse',
}

const STATE_LABELS: Record<CircuitBreakerStatus['state'], string> = {
  closed: 'Closed',
  open: 'Open',
  half_open: 'Half-open',
}

export function CircuitBreakerBadge({ state, className }: CircuitBreakerBadgeProps): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-md border',
        STATE_STYLES[state],
        className
      )}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', STATE_DOT[state])} />
      {STATE_LABELS[state]}
    </span>
  )
}

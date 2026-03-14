import type { CircuitBreakerStatus } from '../../types/provider'
import { cn } from '../../lib/cn'

interface CircuitBreakerBadgeProps {
  state: CircuitBreakerStatus['state']
  className?: string
}

const STATE_STYLES: Record<CircuitBreakerStatus['state'], string> = {
  closed: 'bg-green-100 text-green-700',
  open: 'bg-red-100 text-red-700',
  half_open: 'bg-yellow-100 text-yellow-700',
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
        'text-xs font-medium px-2 py-0.5 rounded-full',
        STATE_STYLES[state],
        className,
      )}
    >
      {STATE_LABELS[state]}
    </span>
  )
}

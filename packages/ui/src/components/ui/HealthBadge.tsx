import type { HealthStatus } from '../../types/provider'
import { cn } from '../../lib/cn'

interface HealthBadgeProps {
  status: HealthStatus['status']
  className?: string
}

const STATUS_STYLES: Record<HealthStatus['status'], string> = {
  healthy: 'bg-success-surface text-success-text border-success/20',
  degraded: 'bg-warning-surface text-warning-text border-warning/20',
  unhealthy: 'bg-danger-surface text-danger-text border-danger/20',
  unknown: 'bg-surface-tertiary text-text-muted border-border',
}

const STATUS_DOT: Record<HealthStatus['status'], string> = {
  healthy: 'bg-success',
  degraded: 'bg-warning',
  unhealthy: 'bg-danger',
  unknown: 'bg-text-faint',
}

const STATUS_LABELS: Record<HealthStatus['status'], string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  unhealthy: 'Unhealthy',
  unknown: 'Unknown',
}

export function HealthBadge({ status, className }: HealthBadgeProps): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-md border',
        STATUS_STYLES[status],
        className
      )}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', STATUS_DOT[status])} />
      {STATUS_LABELS[status]}
    </span>
  )
}

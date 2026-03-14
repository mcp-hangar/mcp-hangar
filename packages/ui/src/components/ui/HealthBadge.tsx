import type { HealthStatus } from '../../types/provider'
import { cn } from '../../lib/cn'

interface HealthBadgeProps {
  status: HealthStatus['status']
  className?: string
}

const STATUS_STYLES: Record<HealthStatus['status'], string> = {
  healthy: 'bg-green-100 text-green-700',
  degraded: 'bg-yellow-100 text-yellow-700',
  unhealthy: 'bg-red-100 text-red-700',
  unknown: 'bg-gray-100 text-gray-500',
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
        'text-xs font-medium px-2 py-0.5 rounded-full',
        STATUS_STYLES[status],
        className,
      )}
    >
      {STATUS_LABELS[status]}
    </span>
  )
}

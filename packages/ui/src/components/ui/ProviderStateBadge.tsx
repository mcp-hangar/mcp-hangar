import type { ProviderState } from '../../types/provider'
import { cn } from '../../lib/cn'

interface ProviderStateBadgeProps {
  state: ProviderState
  className?: string
}

const STATE_STYLES: Record<ProviderState, string> = {
  cold: 'bg-gray-100 text-gray-600',
  initializing: 'bg-blue-100 text-blue-700',
  ready: 'bg-green-100 text-green-700',
  degraded: 'bg-yellow-100 text-yellow-700',
  dead: 'bg-red-100 text-red-700',
}

export function ProviderStateBadge({ state, className }: ProviderStateBadgeProps): JSX.Element {
  const label = state.charAt(0).toUpperCase() + state.slice(1)
  return (
    <span
      className={cn(
        'text-xs font-medium px-2 py-0.5 rounded-full',
        STATE_STYLES[state],
        className,
      )}
    >
      {label}
    </span>
  )
}

import type { ProviderState } from '../../types/provider'
import { cn } from '../../lib/cn'

interface ProviderStateBadgeProps {
  state: ProviderState
  className?: string
}

const STATE_STYLES: Record<ProviderState, string> = {
  cold: 'bg-surface-tertiary text-text-muted border-border',
  initializing: 'bg-accent-surface text-accent-text border-accent/20',
  ready: 'bg-success-surface text-success-text border-success/20',
  degraded: 'bg-warning-surface text-warning-text border-warning/20',
  dead: 'bg-danger-surface text-danger-text border-danger/20',
}

const STATE_DOT: Record<ProviderState, string> = {
  cold: 'bg-text-faint',
  initializing: 'bg-accent animate-pulse',
  ready: 'bg-success',
  degraded: 'bg-warning',
  dead: 'bg-danger',
}

export function ProviderStateBadge({ state, className }: ProviderStateBadgeProps): JSX.Element {
  const label = state.charAt(0).toUpperCase() + state.slice(1)
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-md border',
        STATE_STYLES[state],
        className
      )}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', STATE_DOT[state])} />
      {label}
    </span>
  )
}

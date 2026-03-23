import { cn } from '../../lib/cn'

interface SkeletonProps {
  className?: string
}

/**
 * Animated skeleton placeholder that uses a shimmer effect.
 * Apply width/height via className.
 *
 * Usage:
 *   <Skeleton className="h-4 w-32" />
 *   <Skeleton className="h-10 w-full rounded-lg" />
 */
export function Skeleton({ className }: SkeletonProps): JSX.Element {
  return <div className={cn('animate-skeleton rounded-md bg-surface-tertiary', className)} />
}

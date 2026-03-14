import { cn } from '../../lib/cn'

interface MetricCardProps {
  label: string
  value: string | number
  subLabel?: string
  className?: string
}

export function MetricCard({ label, value, subLabel, className }: MetricCardProps): JSX.Element {
  return (
    <div className={cn('bg-white rounded-lg border border-gray-200 p-4', className)}>
      <p className="text-sm text-gray-500">{label}</p>
      <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
      {subLabel && <p className="text-xs text-gray-400 mt-0.5">{subLabel}</p>}
    </div>
  )
}

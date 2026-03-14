import { BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { EmptyState } from '../ui'
import { cn } from '../../lib/cn'

interface StatDistributionChartProps {
  data: Record<string, number>
  className?: string
}

const STATE_COLOURS: Record<string, string> = {
  ready: '#22c55e',
  cold: '#9ca3af',
  initializing: '#3b82f6',
  degraded: '#f59e0b',
  dead: '#ef4444',
}

const DEFAULT_COLOUR = '#9ca3af'

export function StatDistributionChart({ data, className }: StatDistributionChartProps): JSX.Element {
  const chartData = Object.entries(data).map(([state, count]) => ({
    state,
    count,
    fill: STATE_COLOURS[state] ?? DEFAULT_COLOUR,
  }))

  const hasData = chartData.some((d) => d.count > 0)

  if (!hasData || chartData.length === 0) {
    return <EmptyState message="No provider data." className="py-6" />
  }

  return (
    <div className={cn(className)}>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart layout="vertical" data={chartData}>
          <YAxis dataKey="state" type="category" width={80} />
          <XAxis type="number" allowDecimals={false} />
          <Tooltip />
          <Bar dataKey="count">
            {chartData.map((entry) => (
              <Cell key={entry.state} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

import { motion } from 'framer-motion'
import { BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { EmptyState } from '../ui'
import { cn } from '../../lib/cn'
import { staggerItem } from '../../lib/animations'

interface StatDistributionChartProps {
  data: Record<string, number>
  className?: string
}

/**
 * Chart colours use CSS custom-property values so they respond to theme changes.
 * getComputedStyle reads the current (light or dark) value at render time.
 */
function getCssColor(varName: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
  return value || fallback
}

function getStateColour(state: string): string {
  const map: Record<string, string> = {
    ready: getCssColor('--color-success', '#22c55e'),
    cold: getCssColor('--color-text-faint', '#9ca3af'),
    initializing: getCssColor('--color-accent', '#3b82f6'),
    degraded: getCssColor('--color-warning', '#f59e0b'),
    dead: getCssColor('--color-danger', '#ef4444'),
  }
  return map[state] ?? getCssColor('--color-text-faint', '#9ca3af')
}

export function StatDistributionChart({ data, className }: StatDistributionChartProps): JSX.Element {
  const chartData = Object.entries(data).map(([state, count]) => ({
    state,
    count,
    fill: getStateColour(state),
  }))

  const hasData = chartData.some((d) => d.count > 0)

  if (!hasData || chartData.length === 0) {
    return <EmptyState message="No provider data." className="py-6" />
  }

  return (
    <motion.div variants={staggerItem} className={cn('rounded-xl', className)}>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart layout="vertical" data={chartData} barCategoryGap="20%">
          <YAxis
            dataKey="state"
            type="category"
            width={80}
            tick={{ fontSize: 12, fill: getCssColor('--color-text-muted', '#6b7280') }}
            axisLine={false}
            tickLine={false}
          />
          <XAxis
            type="number"
            allowDecimals={false}
            tick={{ fontSize: 11, fill: getCssColor('--color-text-faint', '#9ca3af') }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: getCssColor('--color-surface-secondary', '#f3f4f6'), opacity: 0.5 }}
            contentStyle={{
              backgroundColor: getCssColor('--color-surface', '#ffffff'),
              border: `1px solid ${getCssColor('--color-border', '#e5e7eb')}`,
              borderRadius: '0.5rem',
              fontSize: '0.75rem',
              boxShadow: '0 4px 12px -2px rgba(0,0,0,0.08)',
            }}
          />
          <Bar dataKey="count" radius={[0, 4, 4, 0]}>
            {chartData.map((entry) => (
              <Cell key={entry.state} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </motion.div>
  )
}

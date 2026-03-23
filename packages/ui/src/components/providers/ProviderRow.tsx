import { motion } from 'framer-motion'
import { Link } from 'react-router'
import { Pencil, Trash2 } from 'lucide-react'
import type { ProviderSummary } from '../../types/provider'
import { ProviderStateBadge, HealthBadge, ActionButton } from '../ui'
import { listRowVariants } from '../../lib/animations'

interface ProviderRowProps {
  provider: ProviderSummary
  onStart: (id: string) => void
  onStop: (id: string) => void
  isStarting: boolean
  isStopping: boolean
  onEdit?: (provider: ProviderSummary) => void
  onDelete?: (provider: ProviderSummary) => void
}

export function ProviderRow({
  provider,
  onStart,
  onStop,
  isStarting,
  isStopping,
  onEdit,
  onDelete,
}: ProviderRowProps): JSX.Element {
  const canStart = provider.state !== 'ready' && provider.state !== 'initializing'
  const canStop = provider.state !== 'cold' && provider.state !== 'dead'

  return (
    <motion.tr
      variants={listRowVariants}
      className="border-b border-border last:border-b-0 transition-colors duration-150 hover:bg-surface-secondary/60"
    >
      <td className="py-3 px-4">
        <Link
          to={`/providers/${provider.provider_id}`}
          className="text-sm font-medium text-accent hover:text-accent/80 transition-colors duration-150"
        >
          {provider.provider_id}
        </Link>
      </td>
      <td className="py-3 px-4">
        <ProviderStateBadge state={provider.state} />
      </td>
      <td className="py-3 px-4">
        <span className="text-sm text-text-muted capitalize font-mono">{provider.mode}</span>
      </td>
      <td className="py-3 px-4 text-sm text-text-muted text-right tabular-nums">{provider.tools_count}</td>
      <td className="py-3 px-4">
        {provider.health ? (
          <HealthBadge status={provider.health.status} />
        ) : (
          <span className="text-sm text-text-faint">&mdash;</span>
        )}
      </td>
      <td className="py-3 px-4">
        <div className="flex items-center gap-1.5">
          <ActionButton
            variant="primary"
            size="sm"
            disabled={!canStart}
            isLoading={isStarting}
            onClick={() => onStart(provider.provider_id)}
          >
            Start
          </ActionButton>
          <ActionButton
            variant="danger"
            size="sm"
            disabled={!canStop}
            isLoading={isStopping}
            onClick={() => onStop(provider.provider_id)}
          >
            Stop
          </ActionButton>
          {onEdit && (
            <button
              type="button"
              onClick={() => onEdit(provider)}
              className="p-1.5 text-text-faint hover:text-text-secondary hover:bg-surface-tertiary rounded-md transition-colors duration-150"
              title="Edit provider"
            >
              <Pencil size={14} />
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={() => onDelete(provider)}
              className="p-1.5 text-text-faint hover:text-danger hover:bg-danger-surface rounded-md transition-colors duration-150"
              title="Delete provider"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </td>
    </motion.tr>
  )
}

import { Link } from 'react-router'
import { Pencil, Trash2 } from 'lucide-react'
import type { ProviderSummary } from '../../types/provider'
import { ProviderStateBadge, HealthBadge, ActionButton } from '../ui'

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
    <tr className="border-b border-gray-100 hover:bg-gray-50">
      <td className="py-2 px-3">
        <Link to={`/providers/${provider.provider_id}`} className="text-sm font-medium text-blue-600 hover:underline">
          {provider.provider_id}
        </Link>
      </td>
      <td className="py-2 px-3">
        <ProviderStateBadge state={provider.state} />
      </td>
      <td className="py-2 px-3 text-sm text-gray-600 capitalize">{provider.mode}</td>
      <td className="py-2 px-3 text-sm text-gray-600 text-right">{provider.tools_count}</td>
      <td className="py-2 px-3">
        {provider.health ? (
          <HealthBadge status={provider.health.status} />
        ) : (
          <span className="text-sm text-gray-400">&mdash;</span>
        )}
      </td>
      <td className="py-2 px-3">
        <div className="flex gap-2">
          <ActionButton
            variant="primary"
            disabled={!canStart}
            isLoading={isStarting}
            onClick={() => onStart(provider.provider_id)}
          >
            Start
          </ActionButton>
          <ActionButton
            variant="danger"
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
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
              title="Edit provider"
            >
              <Pencil size={14} />
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={() => onDelete(provider)}
              className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded"
              title="Delete provider"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

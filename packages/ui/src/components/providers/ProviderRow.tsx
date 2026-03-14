import { Link } from 'react-router'
import type { ProviderSummary } from '../../types/provider'
import { ProviderStateBadge, HealthBadge, ActionButton } from '../ui'

interface ProviderRowProps {
  provider: ProviderSummary
  onStart: (id: string) => void
  onStop: (id: string) => void
  isStarting: boolean
  isStopping: boolean
}

export function ProviderRow({ provider, onStart, onStop, isStarting, isStopping }: ProviderRowProps): JSX.Element {
  const canStart = provider.state !== 'ready' && provider.state !== 'initializing'
  const canStop = provider.state !== 'cold' && provider.state !== 'dead'

  return (
    <tr className="border-b border-gray-100 hover:bg-gray-50">
      <td className="py-2 px-3">
        <Link
          to={`/providers/${provider.provider_id}`}
          className="text-sm font-medium text-blue-600 hover:underline"
        >
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
        </div>
      </td>
    </tr>
  )
}

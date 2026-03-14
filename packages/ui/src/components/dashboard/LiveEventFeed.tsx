import { cn } from '../../lib/cn'
import { useEventStream } from '../../hooks/useEventStream'
import { EmptyState } from '../ui'
import type { WsConnectionStatus } from '../../store/ws'

interface LiveEventFeedProps {
  maxEvents?: number
  className?: string
}

function statusDotClass(status: WsConnectionStatus): string {
  if (status === 'connected') return 'bg-green-400'
  if (status === 'connecting') return 'bg-yellow-400 animate-pulse'
  return 'bg-gray-300'
}

function formatTime(isoString: string): string {
  try {
    const d = new Date(isoString)
    return d.toLocaleTimeString(undefined, { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return isoString
  }
}

export function LiveEventFeed({ maxEvents = 20, className }: LiveEventFeedProps): JSX.Element {
  const { events, status } = useEventStream({ bufferSize: maxEvents })
  const reversed = [...events].reverse()

  return (
    <div className={cn('bg-white rounded-lg border border-gray-200 p-4', className)}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-gray-700">Live Events</span>
        <span className={cn('w-2.5 h-2.5 rounded-full inline-block', statusDotClass(status))} title={status} />
      </div>

      {events.length === 0 ? (
        <EmptyState message="Waiting for events..." className="py-4" />
      ) : (
        <ul className="max-h-64 overflow-y-auto space-y-1.5">
          {reversed.map((event) => (
            <li key={event.event_id} className="flex items-start gap-2">
              <span className="text-xs font-mono text-gray-700 break-all">{event.event_type}</span>
              {event.provider_id && (
                <span className="text-xs text-gray-400 shrink-0">{event.provider_id}</span>
              )}
              <span className="text-xs text-gray-400 shrink-0 ml-auto">
                {formatTime(event.occurred_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

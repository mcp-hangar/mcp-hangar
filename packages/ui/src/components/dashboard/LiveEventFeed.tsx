import { useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../../lib/cn'
import { useEventStream } from '../../hooks/useEventStream'
import { EmptyState } from '../ui'
import type { WsConnectionStatus } from '../../store/ws'

interface LiveEventFeedProps {
  maxEvents?: number
  className?: string
  typeFilter?: string
}

function statusDotClass(status: WsConnectionStatus): string {
  if (status === 'connected') return 'bg-success'
  if (status === 'connecting') return 'bg-warning animate-pulse'
  return 'bg-border-strong'
}

function formatTime(isoString: string): string {
  try {
    const d = new Date(isoString)
    return d.toLocaleTimeString(undefined, { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return isoString
  }
}

export function LiveEventFeed({ maxEvents = 20, className, typeFilter }: LiveEventFeedProps): JSX.Element {
  const { events, status } = useEventStream({ bufferSize: maxEvents })
  const allReversed = [...events].reverse()
  const reversed = useMemo(
    () => (typeFilter ? allReversed.filter((e) => e.event_type === typeFilter) : allReversed),
    [allReversed, typeFilter]
  )

  return (
    <div className={cn('bg-surface rounded-xl border border-border p-5 shadow-xs', className)}>
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm font-medium text-text-secondary">Live Events</span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-faint capitalize">{status}</span>
          <span className="relative flex h-2.5 w-2.5">
            {status === 'connected' && <span className="absolute inset-0 rounded-full bg-success/40 animate-ping" />}
            <span className={cn('relative inline-flex h-2.5 w-2.5 rounded-full', statusDotClass(status))} />
          </span>
        </div>
      </div>

      {events.length === 0 ? (
        <EmptyState message="Waiting for events..." className="py-6" />
      ) : (
        <ul className="max-h-64 overflow-y-auto space-y-0.5">
          <AnimatePresence initial={false}>
            {reversed.map((event) => (
              <motion.li
                key={event.event_id}
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.15 }}
                className="flex items-center gap-2 px-2.5 py-1.5 rounded-md hover:bg-surface-secondary/60 transition-colors duration-100"
              >
                <span className="text-xs font-mono text-text-primary break-all leading-snug">{event.event_type}</span>
                {event.provider_id && (
                  <span className="text-xs text-text-faint shrink-0 px-1.5 py-0.5 bg-surface-secondary rounded">
                    {event.provider_id}
                  </span>
                )}
                <span className="text-xs text-text-faint shrink-0 ml-auto tabular-nums">
                  {formatTime(event.occurred_at)}
                </span>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </div>
  )
}

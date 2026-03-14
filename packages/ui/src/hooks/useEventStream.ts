import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { DomainEvent, EventSubscriptionFilter } from '../types/events'
import { queryKeys } from '../lib/queryKeys'
import { useWsStore } from '../store/ws'
import { useWebSocket } from './useWebSocket'

interface UseEventStreamOptions {
  filter?: EventSubscriptionFilter
  onEvent?: (event: DomainEvent) => void
  /** Max events to keep in the local buffer. Default: 100 */
  bufferSize?: number
  enabled?: boolean
}

interface UseEventStreamReturn {
  events: DomainEvent[]
  status: ReturnType<typeof useWsStore.getState>['eventsStatus']
  clearEvents: () => void
}

// Map event_type prefixes to TanStack Query key roots for cache invalidation.
const EVENT_TYPE_TO_QUERY_KEYS: Array<[string, readonly unknown[]]> = [
  ['ProviderState', queryKeys.providers.all],
  ['provider', queryKeys.providers.all],
  ['HealthCheck', queryKeys.providers.all],
  ['Group', queryKeys.groups.all],
  ['group', queryKeys.groups.all],
  ['Discovery', queryKeys.discovery.all],
  ['discovery', queryKeys.discovery.all],
  ['Config', queryKeys.config.all],
  ['config', queryKeys.config.all],
  ['System', queryKeys.system.all],
]

export function useEventStream({
  filter,
  onEvent,
  bufferSize = 100,
  enabled = true,
}: UseEventStreamOptions = {}): UseEventStreamReturn {
  const queryClient = useQueryClient()
  const setEventsStatus = useWsStore((s) => s.setEventsStatus)
  const eventsStatus = useWsStore((s) => s.eventsStatus)
  const [events, setEvents] = useState<DomainEvent[]>([])
  const filterSentRef = useRef(false)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  // Keep send accessible from within handleMessage for pong responses
  const sendRef = useRef<((data: string) => void) | null>(null)

  const handleMessage = useCallback(
    (msgEvent: MessageEvent) => {
      try {
        const data = JSON.parse(msgEvent.data as string) as Record<string, unknown>

        // Handle ping -- respond with pong
        if (data.type === 'ping') {
          sendRef.current?.(JSON.stringify({ type: 'pong' }))
          return
        }

        // It's a domain event
        const event = data as DomainEvent
        onEventRef.current?.(event)

        // Add to local buffer (FIFO, capped at bufferSize)
        setEvents((prev) => {
          const next = [...prev, event]
          return next.length > bufferSize ? next.slice(next.length - bufferSize) : next
        })

        // Invalidate TanStack Query caches for affected domains
        // This triggers background refetch -- does NOT replace data directly (Pitfall 5)
        for (const [prefix, queryKey] of EVENT_TYPE_TO_QUERY_KEYS) {
          if (typeof event.event_type === 'string' && event.event_type.includes(prefix)) {
            void queryClient.invalidateQueries({ queryKey: queryKey as unknown[] })
            break
          }
        }
      } catch {
        // Non-JSON or malformed message -- skip
      }
    },
    [queryClient, bufferSize],
  )

  const handleOpen = useCallback(() => {
    setEventsStatus('connected')
    filterSentRef.current = false
    // Send subscription filter if configured
    if (filter && !filterSentRef.current) {
      sendRef.current?.(JSON.stringify(filter))
      filterSentRef.current = true
    }
  }, [filter, setEventsStatus])

  const handleClose = useCallback(() => {
    setEventsStatus('disconnected')
  }, [setEventsStatus])

  const handleError = useCallback(() => {
    setEventsStatus('error', 'WebSocket connection error')
  }, [setEventsStatus])

  const { send } = useWebSocket({
    url: '/api/ws/events',
    onMessage: handleMessage,
    onOpen: handleOpen,
    onClose: handleClose,
    onError: handleError,
    enabled,
  })

  // Keep sendRef up to date so handleMessage can use it for pong responses
  useEffect(() => {
    sendRef.current = send
  }, [send])

  const clearEvents = useCallback(() => setEvents([]), [])

  return { events, status: eventsStatus, clearEvents }
}

import { useCallback, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { WsStateMessage } from '../types/events'
import { queryKeys } from '../lib/queryKeys'
import { useWsStore } from '../store/ws'
import { useWebSocket } from './useWebSocket'

interface UseProviderStateReturn {
  snapshot: WsStateMessage | null
  status: ReturnType<typeof useWsStore.getState>['stateStatus']
}

export function useProviderState(
  intervalSeconds?: number,
  enabled = true,
): UseProviderStateReturn {
  const queryClient = useQueryClient()
  const setStateStatus = useWsStore((s) => s.setStateStatus)
  const stateStatus = useWsStore((s) => s.stateStatus)
  const [snapshot, setSnapshot] = useState<WsStateMessage | null>(null)

  const handleMessage = useCallback(
    (msgEvent: MessageEvent) => {
      try {
        const data = JSON.parse(msgEvent.data as string) as Record<string, unknown>
        if (data.type === 'state') {
          const msg = data as unknown as WsStateMessage
          setSnapshot(msg)
          // Invalidate both providers and groups on state update.
          // Consumers use TanStack Query for authoritative data; snapshot is supplementary.
          void queryClient.invalidateQueries({ queryKey: queryKeys.providers.all })
          void queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
        }
      } catch {
        // Non-JSON message -- skip
      }
    },
    [queryClient],
  )

  const handleOpen = useCallback(() => {
    setStateStatus('connected')
  }, [setStateStatus])

  const handleClose = useCallback(() => setStateStatus('disconnected'), [setStateStatus])
  const handleError = useCallback(
    () => setStateStatus('error', 'WebSocket connection error'),
    [setStateStatus],
  )

  const { status, send } = useWebSocket({
    url: '/api/ws/state',
    onMessage: handleMessage,
    onOpen: handleOpen,
    onClose: handleClose,
    onError: handleError,
    enabled,
  })

  // When connection is established, send the interval config if provided.
  // Using an effect that watches status avoids the circular dependency of
  // needing send inside onOpen (which fires before send is returned).
  useEffect(() => {
    if (status === 'connected' && intervalSeconds !== undefined) {
      send(JSON.stringify({ interval: intervalSeconds }))
    }
  }, [status, intervalSeconds, send])

  return { snapshot, status: stateStatus }
}

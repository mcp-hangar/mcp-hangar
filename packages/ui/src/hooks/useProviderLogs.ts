import { useCallback, useState } from 'react'
import { useWebSocket } from './useWebSocket'
import type { WsStatus } from './useWebSocket'

export interface LogLine {
  provider_id: string
  stream: 'stdout' | 'stderr'
  content: string
  /** Unix timestamp (float) as returned by the backend LogLine.recorded_at field. */
  recorded_at: number
}

interface LogMessage {
  type: 'log_line'
  provider_id: string
  stream: 'stdout' | 'stderr'
  content: string
  recorded_at: number
}

interface UseProviderLogsOptions {
  providerId: string
  /** Max lines to keep in buffer. Default: 1000 */
  maxLines?: number
  enabled?: boolean
}

interface UseProviderLogsReturn {
  logs: LogLine[]
  status: WsStatus
  clearLogs: () => void
}

const MAX_LINES_DEFAULT = 1000

export function useProviderLogs({
  providerId,
  maxLines = MAX_LINES_DEFAULT,
  enabled = true,
}: UseProviderLogsOptions): UseProviderLogsReturn {
  const [logs, setLogs] = useState<LogLine[]>([])

  const handleMessage = useCallback(
    (msgEvent: MessageEvent) => {
      try {
        const data = JSON.parse(msgEvent.data as string) as Record<string, unknown>

        if (data.type !== 'log_line') return

        const msg = data as unknown as LogMessage
        const line: LogLine = {
          provider_id: msg.provider_id,
          stream: msg.stream,
          content: msg.content,
          recorded_at: msg.recorded_at,
        }

        setLogs((prev) => {
          const next = [...prev, line]
          return next.length > maxLines ? next.slice(next.length - maxLines) : next
        })
      } catch {
        // Non-JSON or malformed message -- skip
      }
    },
    [maxLines],
  )

  const handleOpen = useCallback(() => {
    // History lines arrive as regular log_line messages on connect -- no special handling needed
  }, [])

  const { status } = useWebSocket({
    url: `/api/ws/providers/${providerId}/logs`,
    onMessage: handleMessage,
    onOpen: handleOpen,
    enabled,
  })

  const clearLogs = useCallback(() => setLogs([]), [])

  return { logs, status, clearLogs }
}

import { useCallback, useEffect, useRef, useState } from 'react'

export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

interface UseWebSocketOptions {
  url: string
  onMessage: (event: MessageEvent) => void
  onOpen?: () => void
  onClose?: () => void
  onError?: (error: Event) => void
  enabled?: boolean
  /** Initial reconnect delay in ms. Default: 1000 */
  reconnectBaseMs?: number
  /** Max reconnect delay in ms. Default: 30000 */
  reconnectMaxMs?: number
}

interface UseWebSocketReturn {
  status: WsStatus
  send: (data: string) => void
  close: () => void
}

export function useWebSocket({
  url,
  onMessage,
  onOpen,
  onClose,
  onError,
  enabled = true,
  reconnectBaseMs = 1000,
  reconnectMaxMs = 30000,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [status, setStatus] = useState<WsStatus>('disconnected')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const shouldReconnectRef = useRef(true)

  // Stable refs for callbacks -- avoid stale closures
  const onMessageRef = useRef(onMessage)
  const onOpenRef = useRef(onOpen)
  const onCloseRef = useRef(onClose)
  const onErrorRef = useRef(onError)
  onMessageRef.current = onMessage
  onOpenRef.current = onOpen
  onCloseRef.current = onClose
  onErrorRef.current = onError

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    if (wsRef.current?.readyState === WebSocket.CONNECTING) return

    setStatus('connecting')
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('connected')
      reconnectAttemptRef.current = 0
      onOpenRef.current?.()
    }

    ws.onmessage = (event) => {
      onMessageRef.current(event)
    }

    ws.onclose = () => {
      setStatus('disconnected')
      onCloseRef.current?.()

      if (shouldReconnectRef.current) {
        const attempt = reconnectAttemptRef.current
        const delay = Math.min(reconnectBaseMs * Math.pow(2, attempt), reconnectMaxMs)
        reconnectAttemptRef.current = attempt + 1
        reconnectTimerRef.current = setTimeout(connect, delay)
      }
    }

    ws.onerror = (event) => {
      setStatus('error')
      onErrorRef.current?.(event)
      // onclose fires after onerror, reconnect logic is there
    }
  }, [url, reconnectBaseMs, reconnectMaxMs])

  useEffect(() => {
    if (!enabled) {
      shouldReconnectRef.current = false
      wsRef.current?.close()
      return
    }

    shouldReconnectRef.current = true
    connect()

    return () => {
      shouldReconnectRef.current = false
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [enabled, connect])

  const send = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data)
    }
  }, [])

  const close = useCallback(() => {
    shouldReconnectRef.current = false
    wsRef.current?.close()
  }, [])

  return { status, send, close }
}

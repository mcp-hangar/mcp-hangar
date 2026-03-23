import { vi } from 'vitest'
import { setWebSocketFactory, type WebSocketConnection } from '../hooks/useWebSocket'

export class MockWebSocket implements WebSocketConnection {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readyState = MockWebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  readonly send = vi.fn<(data: string) => void>()
  readonly close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED
  })

  constructor(public readonly url: string) {}

  emitOpen(): void {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  emitMessage(data: string | Record<string, unknown>): void {
    const payload = typeof data === 'string' ? data : JSON.stringify(data)
    this.onmessage?.(new MessageEvent('message', { data: payload }))
  }

  emitError(): void {
    this.onerror?.(new Event('error'))
  }

  emitClose(): void {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new Event('close') as CloseEvent)
  }
}

export function installMockWebSocketFactory(
  factory?: (url: string, index: number) => MockWebSocket,
): MockWebSocket[] {
  const sockets: MockWebSocket[] = []

  setWebSocketFactory((url) => {
    const socket = factory?.(url, sockets.length) ?? new MockWebSocket(url)
    sockets.push(socket)
    return socket
  })

  return sockets
}

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from '../useWebSocket'
import { installMockWebSocketFactory } from '../../test/websocket'

describe('useWebSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('connects, forwards messages, and only sends after the socket is open', () => {
    const sockets = installMockWebSocketFactory()
    const onMessage = vi.fn()
    const onOpen = vi.fn()

    const { result } = renderHook(() =>
      useWebSocket({
        url: '/api/ws/events',
        onMessage,
        onOpen,
      }),
    )

    expect(result.current.status).toBe('connecting')
    expect(sockets).toHaveLength(1)
    expect(sockets[0].url).toBe('/api/ws/events')

    act(() => {
      result.current.send('before-open')
    })
    expect(sockets[0].send).not.toHaveBeenCalled()

    act(() => {
      sockets[0].emitOpen()
    })

    expect(result.current.status).toBe('connected')
    expect(onOpen).toHaveBeenCalledOnce()

    act(() => {
      result.current.send('hello')
      sockets[0].emitMessage({ type: 'ProviderStateChanged', provider_id: 'math' })
    })

    expect(sockets[0].send).toHaveBeenCalledWith('hello')
    expect(onMessage).toHaveBeenCalledOnce()
    expect(onMessage.mock.calls[0][0].data).toBe(
      JSON.stringify({ type: 'ProviderStateChanged', provider_id: 'math' }),
    )
  })

  it('does not connect while disabled and opens a socket when enabled later', () => {
    const sockets = installMockWebSocketFactory()

    const { result, rerender } = renderHook(
      ({ enabled }) =>
        useWebSocket({
          url: '/api/ws/state',
          onMessage: vi.fn(),
          enabled,
        }),
      { initialProps: { enabled: false } },
    )

    expect(result.current.status).toBe('disconnected')
    expect(sockets).toHaveLength(0)

    rerender({ enabled: true })

    expect(result.current.status).toBe('connecting')
    expect(sockets).toHaveLength(1)
  })

  it('reconnects with exponential backoff after unexpected closes', () => {
    const sockets = installMockWebSocketFactory()

    renderHook(() =>
      useWebSocket({
        url: '/api/ws/events',
        onMessage: vi.fn(),
        reconnectBaseMs: 100,
        reconnectMaxMs: 250,
      }),
    )

    act(() => {
      sockets[0].emitOpen()
      sockets[0].emitClose()
    })

    act(() => {
      vi.advanceTimersByTime(99)
    })
    expect(sockets).toHaveLength(1)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(sockets).toHaveLength(2)

    act(() => {
      sockets[1].emitClose()
    })

    act(() => {
      vi.advanceTimersByTime(199)
    })
    expect(sockets).toHaveLength(2)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(sockets).toHaveLength(3)
  })

  it('close disables reconnect attempts', () => {
    const sockets = installMockWebSocketFactory()

    const { result } = renderHook(() =>
      useWebSocket({
        url: '/api/ws/events',
        onMessage: vi.fn(),
        reconnectBaseMs: 100,
      }),
    )

    act(() => {
      sockets[0].emitOpen()
      result.current.close()
      sockets[0].emitClose()
      vi.advanceTimersByTime(1_000)
    })

    expect(sockets[0].close).toHaveBeenCalledOnce()
    expect(sockets).toHaveLength(1)
  })
})

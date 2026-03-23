import { act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useEventStream } from '../useEventStream'
import { buildDomainEvent } from '../../test/fixtures'
import { createTestQueryClient, renderHookWithProviders, waitFor } from '../../test/render'
import { installMockWebSocketFactory } from '../../test/websocket'

describe('useEventStream', () => {
  it('sends the subscription filter on open and keeps only the newest buffered events', async () => {
    const sockets = installMockWebSocketFactory()
    const filter = { event_types: ['ProviderStateChanged'], provider_ids: ['math'] }

    const { result } = renderHookWithProviders(() =>
      useEventStream({
        filter,
        bufferSize: 2,
      }),
    )

    await act(async () => {
      sockets[0].emitOpen()
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(sockets[0].send).toHaveBeenCalledWith(JSON.stringify(filter))
    })

    await act(async () => {
      sockets[0].emitMessage(buildDomainEvent({ event_id: 'evt-1' }))
      sockets[0].emitMessage(buildDomainEvent({ event_id: 'evt-2' }))
      sockets[0].emitMessage(buildDomainEvent({ event_id: 'evt-3' }))
      await Promise.resolve()
    })

    expect(result.current.status).toBe('connected')
    expect(result.current.events.map((event) => event.event_id)).toEqual(['evt-2', 'evt-3'])
  })

  it('responds to ping messages with pong', async () => {
    const sockets = installMockWebSocketFactory()

    renderHookWithProviders(() => useEventStream())

    await act(async () => {
      sockets[0].emitOpen()
      sockets[0].emitMessage({ type: 'ping' })
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(sockets[0].send).toHaveBeenCalledWith(JSON.stringify({ type: 'pong' }))
    })
  })

  it('invalidates provider queries for provider events and ignores malformed messages', async () => {
    const sockets = installMockWebSocketFactory()
    const queryClient = createTestQueryClient()
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHookWithProviders(() => useEventStream({ bufferSize: 5 }), {
      queryClient,
    })

    await act(async () => {
      sockets[0].emitOpen()
      sockets[0].emitMessage(buildDomainEvent({ event_type: 'ProviderStateChanged' }))
      sockets[0].emitMessage('not-json')
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['providers'] })
    })

    expect(result.current.events).toHaveLength(1)
    expect(result.current.events[0].event_type).toBe('ProviderStateChanged')
  })
})

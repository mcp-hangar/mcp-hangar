import '@testing-library/jest-dom/vitest'
import {cleanup} from '@testing-library/react'
import {afterAll, afterEach, beforeAll, vi} from 'vitest'
import {server} from './msw/server'
import {useWsStore} from '../store/ws'
import {resetWebSocketFactory} from '../hooks/useWebSocket'

// Stub ResizeObserver for Recharts and other components that depend on it
class ResizeObserverStub {
    observe(): void {
    }

    unobserve(): void {
    }

    disconnect(): void {
    }
}

window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

Element.prototype.scrollIntoView = vi.fn()

// MSW lifecycle
beforeAll(() => server.listen({
    onUnhandledRequest(request, print) {
        // Suppress warnings for WebSocket upgrade requests (not mockable via http handlers)
        if (request.url.includes('/ws/')) return
        print.warning()
    },
}))
afterEach(() => {
    server.resetHandlers()
    useWsStore.setState({
        eventsStatus: 'disconnected',
        stateStatus: 'disconnected',
        eventsError: null,
        stateError: null,
    })
    resetWebSocketFactory()
    vi.restoreAllMocks()
    cleanup()
})
afterAll(() => server.close())

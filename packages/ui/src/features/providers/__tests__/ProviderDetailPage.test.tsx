import { Route, Routes } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ProviderDetailPage } from '../ProviderDetailPage'
import { renderWithProviders, screen, waitFor } from '../../../test/render'
import { server } from '../../../test/msw/server'
import { buildHealthInfo, buildLogLine, buildProviderDetails, buildToolInfo } from '../../../test/fixtures'
import { useProviderLogs } from '../../../hooks/useProviderLogs'

vi.mock('../../../hooks/useProviderLogs', () => ({
  useProviderLogs: vi.fn(() => ({
    logs: [],
    status: 'disconnected',
    clearLogs: vi.fn(),
  })),
}))

const mockedUseProviderLogs = vi.mocked(useProviderLogs)

function renderDetailPage(initialEntry = '/providers/math') {
  return renderWithProviders(
    <Routes>
      <Route path="/providers/:id" element={<ProviderDetailPage />} />
    </Routes>,
    { initialEntries: [initialEntry] }
  )
}

describe('ProviderDetailPage', () => {
  it('shows a loading spinner while provider details are loading', () => {
    server.use(
      http.get('/api/providers/:id', async () => {
        await new Promise((resolve) => setTimeout(resolve, 10_000))
        return HttpResponse.json(buildProviderDetails())
      })
    )

    renderDetailPage()

    expect(document.querySelector('svg.animate-spin')).toBeInTheDocument()
  })

  it('renders provider details, health data, tools, and logs when expanded', async () => {
    const clearLogs = vi.fn()
    mockedUseProviderLogs.mockReturnValue({
      logs: [buildLogLine({ content: 'boot complete' })],
      status: 'connected',
      clearLogs,
    })

    server.use(
      http.get('/api/providers/:id', ({ params }) => {
        return HttpResponse.json(
          buildProviderDetails({
            provider_id: String(params.id),
            state: 'degraded',
            mode: 'docker',
            idle_ttl_s: 300,
            command: ['python', '-m', 'providers.math'],
            circuit_breaker: {
              state: 'open',
              failure_count: 5,
              opened_at: '2026-03-20T10:30:00Z',
            },
            tools: [
              buildToolInfo({
                name: 'calculate',
                description: 'Performs calculations',
              }),
            ],
          })
        )
      }),
      http.get('/api/providers/:id/health', () => {
        return HttpResponse.json(
          buildHealthInfo({
            status: 'degraded',
            consecutive_failures: 3,
          })
        )
      })
    )

    const { user } = renderDetailPage('/providers/calc')

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'calc' })).toBeInTheDocument()
    })

    expect(screen.getByText('docker')).toBeInTheDocument()
    expect(screen.getByText('300s')).toBeInTheDocument()
    expect(screen.getByText('python -m providers.math')).toBeInTheDocument()
    expect(screen.getAllByText('Degraded')).toHaveLength(2)
    expect(screen.getByText('3 consecutive failures')).toBeInTheDocument()
    expect(screen.getByText('Open')).toBeInTheDocument()
    expect(screen.getByText('5 failures')).toBeInTheDocument()
    expect(screen.getByText('calculate')).toBeInTheDocument()
    expect(screen.getByText('Performs calculations')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Process Logs' }))

    expect(screen.getByText('boot complete')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(clearLogs).toHaveBeenCalledOnce()
  })

  it('renders not found state when provider details are unavailable', async () => {
    server.use(http.get('/api/providers/:id', () => new HttpResponse(null, { status: 404 })))

    renderDetailPage('/providers/missing')

    await waitFor(() => {
      expect(screen.getByText("Provider 'missing' not found.")).toBeInTheDocument()
    })
  })

  it('starts a cold provider when Start is clicked', async () => {
    let startedProviderId: string | null = null

    server.use(
      http.get('/api/providers/:id', ({ params }) => {
        return HttpResponse.json(
          buildProviderDetails({
            provider_id: String(params.id),
            state: 'cold',
          })
        )
      }),
      http.post('/api/providers/:id/start', ({ params }) => {
        startedProviderId = String(params.id)
        return HttpResponse.json({ status: 'started', provider_id: params.id })
      })
    )

    const { user } = renderDetailPage('/providers/cold-provider')

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'cold-provider' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start' }))

    await waitFor(() => {
      expect(startedProviderId).toBe('cold-provider')
    })
  })

  it('stops a ready provider when Stop is clicked', async () => {
    let stoppedProviderId: string | null = null

    server.use(
      http.get('/api/providers/:id', ({ params }) => {
        return HttpResponse.json(
          buildProviderDetails({
            provider_id: String(params.id),
            state: 'ready',
          })
        )
      }),
      http.post('/api/providers/:id/stop', ({ params }) => {
        stoppedProviderId = String(params.id)
        return HttpResponse.json({ status: 'stopped', provider_id: params.id })
      })
    )

    const { user } = renderDetailPage('/providers/ready-provider')

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'ready-provider' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Stop' }))

    await waitFor(() => {
      expect(stoppedProviderId).toBe('ready-provider')
    })
  })
})

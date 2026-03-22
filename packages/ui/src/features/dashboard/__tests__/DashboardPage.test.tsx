import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { DashboardPage } from '../DashboardPage'
import { renderWithProviders, screen, waitFor } from '../../../test/render'
import { server } from '../../../test/msw/server'

describe('DashboardPage', () => {
  it('renders metric cards with data from API', async () => {
    renderWithProviders(<DashboardPage />)

    await waitFor(() => {
      expect(screen.getByText('Total Providers')).toBeInTheDocument()
    })

    // Default fixture: total_providers=5, ready=3, total_tool_calls=142, error_rate=0.02
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('142')).toBeInTheDocument()
    // error_rate = Math.round(142*0.02)/142 = 3/142 ~ 2.1%
    expect(screen.getByText('2.1%')).toBeInTheDocument()
  })

  it('renders all four metric card labels', async () => {
    renderWithProviders(<DashboardPage />)

    await waitFor(() => {
      expect(screen.getByText('Total Providers')).toBeInTheDocument()
    })

    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(screen.getByText('Tool Calls')).toBeInTheDocument()
    expect(screen.getByText('Error Rate')).toBeInTheDocument()
  })

  it('renders active alerts section', async () => {
    renderWithProviders(<DashboardPage />)

    await waitFor(() => {
      expect(screen.getByText('Active Alerts')).toBeInTheDocument()
    })

    // From handlers: alert a-1 has resolved_at=null (active), a-2 has resolved_at (resolved)
    // So only 1 active alert should be shown
    await waitFor(() => {
      expect(screen.getByText('Provider math is dead')).toBeInTheDocument()
    })

    // The resolved alert should not be shown
    expect(screen.queryByText('High error rate')).not.toBeInTheDocument()
  })

  it('shows "No active alerts" when all alerts are resolved', async () => {
    server.use(
      http.get('/api/observability/alerts/', () => {
        return HttpResponse.json({
          alerts: [
            {
              alert_id: 'a-1',
              level: 'warning',
              message: 'Resolved issue',
              provider_id: 'test',
              event_type: 'test',
              timestamp: '2026-03-20T10:00:00Z',
              created_at: '2026-03-20T10:00:00Z',
              resolved_at: '2026-03-20T11:00:00Z',
            },
          ],
        })
      }),
    )

    renderWithProviders(<DashboardPage />)

    await waitFor(() => {
      expect(screen.getByText('No active alerts.')).toBeInTheDocument()
    })
  })

  it('shows loading spinner while metrics are loading', () => {
    // Delay the system API response to keep loading state
    server.use(
      http.get('/api/system/', async () => {
        await new Promise((r) => setTimeout(r, 10_000))
        return HttpResponse.json({})
      }),
    )

    renderWithProviders(<DashboardPage />)

    const spinners = document.querySelectorAll('svg.animate-spin')
    expect(spinners.length).toBeGreaterThan(0)
  })

  it('renders Provider States chart section', async () => {
    renderWithProviders(<DashboardPage />)

    await waitFor(() => {
      expect(screen.getByText('Provider States')).toBeInTheDocument()
    })
  })

  it('handles API error for metrics gracefully', async () => {
    server.use(
      http.get('/api/system/', () => {
        return new HttpResponse(null, { status: 500 })
      }),
    )

    // Should not throw; TanStack Query handles errors
    renderWithProviders(<DashboardPage />)

    // Wait a bit to ensure no crash
    await waitFor(() => {
      expect(screen.getByText('Active Alerts')).toBeInTheDocument()
    })
  })
})

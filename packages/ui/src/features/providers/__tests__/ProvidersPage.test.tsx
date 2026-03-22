import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ProvidersPage } from '../ProvidersPage'
import { renderWithProviders, screen, waitFor } from '../../../test/render'
import { server } from '../../../test/msw/server'
import { buildProviderSummary } from '../../../test/fixtures'

describe('ProvidersPage', () => {
  it('renders provider list from API', async () => {
    renderWithProviders(<ProvidersPage />)

    // Wait for data to load
    await waitFor(() => {
      expect(screen.getByText('math')).toBeInTheDocument()
    })

    expect(screen.getByText('search')).toBeInTheDocument()
    expect(screen.getByText('cache')).toBeInTheDocument()
  })

  it('shows loading spinner initially', () => {
    renderWithProviders(<ProvidersPage />)
    // LoadingSpinner renders an SVG with animate-spin class
    const spinners = document.querySelectorAll('svg.animate-spin')
    expect(spinners.length).toBeGreaterThan(0)
  })

  it('shows error message on API failure', async () => {
    server.use(
      http.get('/api/providers/', () => {
        return new HttpResponse(null, { status: 500 })
      }),
    )

    renderWithProviders(<ProvidersPage />)

    await waitFor(() => {
      expect(screen.getByText('Failed to load providers.')).toBeInTheDocument()
    })
  })

  it('shows empty state when no providers match filter', async () => {
    server.use(
      http.get('/api/providers/', () => {
        return HttpResponse.json({ providers: [] })
      }),
    )

    renderWithProviders(<ProvidersPage />)

    await waitFor(() => {
      expect(screen.getByText('No providers match the filter.')).toBeInTheDocument()
    })
  })

  it('filters providers when clicking a state tab', async () => {
    // Override handler to track the state parameter
    let capturedState: string | null = null
    server.use(
      http.get('/api/providers/', ({ request }) => {
        const url = new URL(request.url)
        capturedState = url.searchParams.get('state')
        const providers = capturedState === 'ready'
          ? [buildProviderSummary({ provider_id: 'math', state: 'ready' })]
          : [
              buildProviderSummary({ provider_id: 'math', state: 'ready' }),
              buildProviderSummary({ provider_id: 'search', state: 'cold' }),
            ]
        return HttpResponse.json({ providers })
      }),
    )

    const { user } = renderWithProviders(<ProvidersPage />)

    // Wait for initial load
    await waitFor(() => {
      expect(screen.getByText('math')).toBeInTheDocument()
    })

    // Click the "Ready" filter tab
    await user.click(screen.getByRole('button', { name: 'Ready' }))

    // Wait for filtered results
    await waitFor(() => {
      expect(capturedState).toBe('ready')
    })
  })

  it('renders filter tabs', async () => {
    renderWithProviders(<ProvidersPage />)

    // All filter buttons should be present
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ready' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cold' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Degraded' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dead' })).toBeInTheDocument()
  })

  it('renders table headers', async () => {
    renderWithProviders(<ProvidersPage />)

    await waitFor(() => {
      expect(screen.getByText('math')).toBeInTheDocument()
    })

    expect(screen.getByText('Provider')).toBeInTheDocument()
    expect(screen.getByText('State')).toBeInTheDocument()
    expect(screen.getByText('Mode')).toBeInTheDocument()
    expect(screen.getByText('Tools')).toBeInTheDocument()
    expect(screen.getByText('Health')).toBeInTheDocument()
    expect(screen.getByText('Actions')).toBeInTheDocument()
  })
})

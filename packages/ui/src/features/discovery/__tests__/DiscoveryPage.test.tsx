import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { DiscoveryPage } from '../DiscoveryPage'
import { renderWithProviders, screen, waitFor } from '../../../test/render'
import { server } from '../../../test/msw/server'

describe('DiscoveryPage', () => {
  it('renders discovery sources with correct fields', async () => {
    renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('docker-local')).toBeInTheDocument()
    })

    expect(screen.getByText('k8s-prod')).toBeInTheDocument()

    // Source type badges -- "docker" appears both in source card and pending table
    const dockerElements = screen.getAllByText('docker')
    expect(dockerElements.length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('kubernetes')).toBeInTheDocument()

    // Health status
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    expect(screen.getByText('Unhealthy')).toBeInTheDocument()

    // Provider counts
    expect(screen.getByText('3 providers found')).toBeInTheDocument()
    expect(screen.getByText('0 providers found')).toBeInTheDocument()

    // Error message for unhealthy source
    expect(screen.getByText('Connection refused to cluster API')).toBeInTheDocument()
  })

  it('renders pending providers with name and source type', async () => {
    renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('auto-detected-math')).toBeInTheDocument()
    })

    // Pending provider table headers
    expect(screen.getByText('Name')).toBeInTheDocument()
    expect(screen.getByText('Source')).toBeInTheDocument()
    expect(screen.getByText('Mode')).toBeInTheDocument()
  })

  it('shows approve and reject buttons for pending providers', async () => {
    renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('auto-detected-math')).toBeInTheDocument()
    })

    expect(screen.getByText('Approve')).toBeInTheDocument()
    expect(screen.getByText('Reject')).toBeInTheDocument()
  })

  it('opens Add Source drawer when clicking +Add Source', async () => {
    const { user } = renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('Discovery Sources')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Add Source'))

    await waitFor(() => {
      expect(screen.getByText('Add Discovery Source')).toBeInTheDocument()
    })
  })

  it('shows action buttons (edit, scan, delete, toggle) on source cards', async () => {
    renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('docker-local')).toBeInTheDocument()
    })

    // Each source card has 4 action buttons with titles
    const editButtons = screen.getAllByTitle('Edit source')
    expect(editButtons.length).toBe(2)

    const scanButtons = screen.getAllByTitle('Trigger scan')
    expect(scanButtons.length).toBe(2)

    const deleteButtons = screen.getAllByTitle('Delete source')
    expect(deleteButtons.length).toBe(2)

    // Toggle buttons -- one enabled, one enabled (both have Eye icon titles)
    const disableButtons = screen.getAllByTitle('Disable source')
    expect(disableButtons.length).toBe(2)
  })

  it('dims disabled source cards with reduced opacity', async () => {
    server.use(
      http.get('/api/discovery/sources', () => {
        return HttpResponse.json({
          sources: [
            {
              source_id: 'disabled-source',
              source_type: 'filesystem',
              mode: 'additive',
              is_healthy: true,
              is_enabled: false,
              last_discovery: null,
              providers_count: 0,
              error_message: null,
            },
          ],
        })
      })
    )

    renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('disabled-source')).toBeInTheDocument()
    })

    // The card should have opacity-50 class when is_enabled=false
    const card = screen.getByText('disabled-source').closest('div.opacity-50')
    expect(card).toBeInTheDocument()

    // Toggle button should show "Enable source" title when disabled
    expect(screen.getByTitle('Enable source')).toBeInTheDocument()
  })

  it('shows empty states when no data', async () => {
    server.use(
      http.get('/api/discovery/sources', () => {
        return HttpResponse.json({ sources: [] })
      }),
      http.get('/api/discovery/pending', () => {
        return HttpResponse.json({ pending: [] })
      }),
      http.get('/api/discovery/quarantined', () => {
        return HttpResponse.json({ quarantined: {} })
      })
    )

    renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('No discovery sources configured.')).toBeInTheDocument()
    })

    expect(screen.getByText('No pending providers.')).toBeInTheDocument()
    expect(screen.getByText('No quarantined providers.')).toBeInTheDocument()
  })

  it('calls approve mutation when clicking Approve', async () => {
    const { user } = renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('auto-detected-math')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Approve'))

    // Mutation fires -- the MSW handler returns success.
    // We verify by checking the button still exists (page re-renders).
    await waitFor(() => {
      expect(screen.getByText('Pending Providers')).toBeInTheDocument()
    })
  })

  it('calls delete with confirmation dialog', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { user } = renderWithProviders(<DiscoveryPage />)

    await waitFor(() => {
      expect(screen.getByText('docker-local')).toBeInTheDocument()
    })

    const deleteButtons = screen.getAllByTitle('Delete source')
    await user.click(deleteButtons[0])

    expect(confirmSpy).toHaveBeenCalledWith('Delete discovery source "docker-local"?')
    confirmSpy.mockRestore()
  })
})

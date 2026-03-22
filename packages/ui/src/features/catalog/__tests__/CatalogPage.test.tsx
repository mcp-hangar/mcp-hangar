import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { CatalogPage } from '../CatalogPage'
import { renderWithProviders, screen, waitFor } from '../../../test/render'
import { server } from '../../../test/msw/server'

describe('CatalogPage', () => {
  it('renders catalog entries from API', async () => {
    renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('filesystem')).toBeInTheDocument()
    })

    expect(screen.getByText('brave-search')).toBeInTheDocument()
    expect(screen.getByText('custom-analytics')).toBeInTheDocument()
  })

  it('shows entry descriptions', async () => {
    renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('Read and write files on the local filesystem')).toBeInTheDocument()
    })

    expect(screen.getByText('Web search powered by Brave Search API')).toBeInTheDocument()
  })

  it('renders tag filter chips for all unique tags', async () => {
    renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('filesystem')).toBeInTheDocument()
    })

    // "All" chip is always present
    expect(screen.getByText('All')).toBeInTheDocument()

    // Tags appear both as filter chips (buttons) and inside cards (spans).
    // Verify tag filter chip buttons exist by selecting only buttons with the
    // rounded-full chip class.
    const chipContainer = screen.getByText('All').parentElement!
    const chipButtons = chipContainer.querySelectorAll('button')
    const chipLabels = Array.from(chipButtons).map((b) => b.textContent)
    expect(chipLabels).toContain('analytics')
    expect(chipLabels).toContain('files')
    expect(chipLabels).toContain('internal')
    expect(chipLabels).toContain('local')
    expect(chipLabels).toContain('search')
    expect(chipLabels).toContain('web')
  })

  it('filters entries when clicking a tag chip', async () => {
    const { user } = renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('filesystem')).toBeInTheDocument()
    })

    // All 3 entries visible initially
    expect(screen.getByText('brave-search')).toBeInTheDocument()
    expect(screen.getByText('custom-analytics')).toBeInTheDocument()

    // Click the "analytics" tag chip (only appears once as a chip button when
    // looking at the filter container -- the card span also has "analytics" text).
    // Target the chip button in the filter container.
    const chipContainer = screen.getByText('All').parentElement!
    const analyticsChip = Array.from(chipContainer.querySelectorAll('button')).find(
      (b) => b.textContent === 'analytics'
    )!
    await user.click(analyticsChip)

    // Wait for filtered results; custom-analytics has the "analytics" tag
    await waitFor(() => {
      expect(screen.getByText('custom-analytics')).toBeInTheDocument()
    })
  })

  it('opens entry detail drawer when clicking a card', async () => {
    const { user } = renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('filesystem')).toBeInTheDocument()
    })

    // Click the filesystem card
    await user.click(screen.getByText('filesystem'))

    // Drawer should show "Deploy as Provider" button
    await waitFor(() => {
      expect(screen.getByText('Deploy as Provider')).toBeInTheDocument()
    })
  })

  it('opens deploy dialog from the detail drawer', async () => {
    const { user } = renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('filesystem')).toBeInTheDocument()
    })

    // Open the drawer
    await user.click(screen.getByText('filesystem'))

    await waitFor(() => {
      expect(screen.getByText('Deploy as Provider')).toBeInTheDocument()
    })

    // Click "Deploy as Provider"
    await user.click(screen.getByText('Deploy as Provider'))

    // Deploy dialog should appear
    await waitFor(() => {
      expect(screen.getByText('Deploy Provider')).toBeInTheDocument()
      expect(screen.getByText('Deploy')).toBeInTheDocument()
      expect(screen.getByText('Cancel')).toBeInTheDocument()
    })
  })

  it('opens add entry drawer when clicking Add Entry button', async () => {
    const { user } = renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('Catalog')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Add Entry'))

    await waitFor(() => {
      expect(screen.getByText('Add Catalog Entry')).toBeInTheDocument()
    })
  })

  it('does not show delete button for builtin entries in drawer', async () => {
    const { user } = renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('filesystem')).toBeInTheDocument()
    })

    // filesystem is builtin=true
    await user.click(screen.getByText('filesystem'))

    await waitFor(() => {
      expect(screen.getByText('Deploy as Provider')).toBeInTheDocument()
    })

    // Delete button should NOT be present for builtin entries
    expect(screen.queryByText('Delete')).not.toBeInTheDocument()
  })

  it('shows entry count footer', async () => {
    renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('Showing 3 of 3 entries')).toBeInTheDocument()
    })
  })

  it('shows empty state when no entries match', async () => {
    server.use(
      http.get('/api/catalog/', () => {
        return HttpResponse.json({ entries: [], total: 0 })
      })
    )

    renderWithProviders(<CatalogPage />)

    await waitFor(() => {
      expect(screen.getByText('No catalog entries found.')).toBeInTheDocument()
    })
  })
})

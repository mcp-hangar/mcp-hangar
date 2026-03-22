import { describe, it, expect } from 'vitest'
import { ConfigPage } from '../ConfigPage'
import { renderWithProviders, screen, waitFor } from '../../../test/render'

describe('ConfigPage', () => {
  it('renders heading and all three tabs', () => {
    renderWithProviders(<ConfigPage />)

    expect(screen.getByText('Configuration')).toBeInTheDocument()
    expect(screen.getByText('Current Config')).toBeInTheDocument()
    expect(screen.getByText('Export & Backup')).toBeInTheDocument()
    expect(screen.getByText('Diff')).toBeInTheDocument()
  })

  it('shows current config tab by default with JSON data', async () => {
    renderWithProviders(<ConfigPage />)

    // MSW handler returns mock config with math and weather providers
    await waitFor(() => {
      expect(screen.getByText(/"math"/)).toBeInTheDocument()
    })

    expect(screen.getByText('Hot Reload')).toBeInTheDocument()
  })

  it('switches to Export & Backup tab', async () => {
    const { user } = renderWithProviders(<ConfigPage />)

    await user.click(screen.getByText('Export & Backup'))

    expect(screen.getByText('Export Configuration')).toBeInTheDocument()
    expect(screen.getByText('Export YAML')).toBeInTheDocument()
    expect(screen.getByText(/rotating backup/)).toBeInTheDocument()
  })

  it('switches to Diff tab and shows diff data', async () => {
    const { user } = renderWithProviders(<ConfigPage />)

    await user.click(screen.getByText('Diff'))

    // MSW handler returns has_diff: true with a unified diff
    await waitFor(() => {
      expect(screen.getByText(/--- on-disk/)).toBeInTheDocument()
    })

    expect(screen.getByText(/\+\+\+ in-memory/)).toBeInTheDocument()
    expect(screen.getByText('Refresh')).toBeInTheDocument()
  })

  it('export button triggers export and shows YAML output', async () => {
    const { user } = renderWithProviders(<ConfigPage />)

    await user.click(screen.getByText('Export & Backup'))
    await user.click(screen.getByText('Export YAML'))

    // MSW returns YAML string with providers
    await waitFor(() => {
      expect(screen.getByText(/idle_ttl_s: 300/)).toBeInTheDocument()
    })

    // Copy and Download buttons should appear
    expect(screen.getByText('Copy')).toBeInTheDocument()
    expect(screen.getByText('Download')).toBeInTheDocument()
  })

  it('diff tab shows colored diff lines', async () => {
    const { user } = renderWithProviders(<ConfigPage />)

    await user.click(screen.getByText('Diff'))

    // Wait for diff to load -- MSW returns diff with +/- lines
    await waitFor(() => {
      expect(screen.getByText(/idle_ttl_s: 300/)).toBeInTheDocument()
    })
  })

  it('current config tab has hot reload button', () => {
    renderWithProviders(<ConfigPage />)

    expect(screen.getByText('Hot Reload')).toBeInTheDocument()
  })

  it('shows helper text on current config tab', () => {
    renderWithProviders(<ConfigPage />)

    expect(screen.getByText(/Configuration is read-only in the UI/)).toBeInTheDocument()
  })

  it('export tab shows backup description', async () => {
    const { user } = renderWithProviders(<ConfigPage />)

    await user.click(screen.getByText('Export & Backup'))

    expect(screen.getByText(/Create a rotating backup of the config file/)).toBeInTheDocument()
  })

  it('diff tab shows description text', async () => {
    const { user } = renderWithProviders(<ConfigPage />)

    await user.click(screen.getByText('Diff'))

    expect(screen.getByText(/Compare the on-disk config file/)).toBeInTheDocument()
  })
})

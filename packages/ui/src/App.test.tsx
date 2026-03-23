import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { renderWithProviders, screen, waitFor } from './test/render'
vi.mock('./components/dashboard/LiveEventFeed', () => ({
  LiveEventFeed: () => <div>Live events test stub</div>,
}))
describe('App routing', () => {
  it('renders layout chrome and dashboard on the root route', async () => {
    renderWithProviders(<App />)
    expect(screen.getByText('MCP Hangar')).toBeInTheDocument()
    expect(screen.getByText('Management Console')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('Total Providers')).toBeInTheDocument()
    })
    expect(screen.getByText('Live events test stub')).toBeInTheDocument()
  })
  it('renders providers page for the providers route', async () => {
    renderWithProviders(<App />, { initialEntries: ['/providers'] })
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'math' })).toBeInTheDocument()
    })
    expect(screen.getByRole('heading', { name: 'Providers' })).toBeInTheDocument()
  })
  it('renders provider detail page for a deep-linked provider route', async () => {
    renderWithProviders(<App />, { initialEntries: ['/providers/math'] })
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'math' })).toBeInTheDocument()
    })
    expect(screen.getByText('Process Logs')).toBeInTheDocument()
    expect(screen.getByText('math-tool')).toBeInTheDocument()
  })
})

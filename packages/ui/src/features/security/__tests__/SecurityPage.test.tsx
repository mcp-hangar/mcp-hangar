import { describe, it, expect } from 'vitest'
import { SecurityPage } from '../SecurityPage'
import { renderWithProviders, screen, waitFor } from '../../../test/render'

describe('SecurityPage', () => {
  it('renders all three tabs', () => {
    renderWithProviders(<SecurityPage />)

    expect(screen.getByText('Events')).toBeInTheDocument()
    expect(screen.getByText('Roles')).toBeInTheDocument()
    expect(screen.getByText('Principals')).toBeInTheDocument()
  })

  it('shows events tab by default', () => {
    renderWithProviders(<SecurityPage />)

    // Events tab content: auto-refreshes message is visible on events tab
    expect(screen.getByText('Auto-refreshes every 30s')).toBeInTheDocument()
  })

  it('switching to Roles tab shows role list from API', async () => {
    const { user } = renderWithProviders(<SecurityPage />)

    await user.click(screen.getByText('Roles'))

    // MSW returns 7 roles (6 builtin + 1 custom)
    await waitFor(() => {
      expect(screen.getByText('admin')).toBeInTheDocument()
    })

    expect(screen.getByText('provider-admin')).toBeInTheDocument()
    expect(screen.getByText('developer')).toBeInTheDocument()
    expect(screen.getByText('viewer')).toBeInTheDocument()
    expect(screen.getByText('auditor')).toBeInTheDocument()
    expect(screen.getByText('service-account')).toBeInTheDocument()
    expect(screen.getByText('custom-operator')).toBeInTheDocument()
  })

  it('switching to Principals tab shows principals list', async () => {
    const { user } = renderWithProviders(<SecurityPage />)

    await user.click(screen.getByText('Principals'))

    await waitFor(() => {
      expect(screen.getByText('user:alice')).toBeInTheDocument()
    })

    expect(screen.getByText('user:bob')).toBeInTheDocument()
    expect(screen.getByText('service:ci-bot')).toBeInTheDocument()
  })

  it('displays heading', () => {
    renderWithProviders(<SecurityPage />)

    expect(screen.getByText('Security')).toBeInTheDocument()
  })
})

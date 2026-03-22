import { describe, it, expect } from 'vitest'
import { AuthPage } from '../AuthPage'
import { renderWithProviders, screen } from '../../../test/render'

describe('AuthPage', () => {
  it('renders API Keys heading', () => {
    renderWithProviders(<AuthPage />)

    expect(screen.getByText('API Keys')).toBeInTheDocument()
  })

  it('shows Create Key button', () => {
    renderWithProviders(<AuthPage />)

    expect(screen.getByText('Create Key')).toBeInTheDocument()
  })

  it('does not show Roles tab', () => {
    renderWithProviders(<AuthPage />)

    // Roles tab was removed from AuthPage -- only Security page has it now
    expect(screen.queryByText('Roles')).not.toBeInTheDocument()
  })

  it('shows search prompt when no principal is searched', () => {
    renderWithProviders(<AuthPage />)

    expect(screen.getByText('Enter a principal ID above and press Search to list API keys.')).toBeInTheDocument()
  })

  it('has principal ID search input', () => {
    renderWithProviders(<AuthPage />)

    expect(screen.getByPlaceholderText('Filter by principal ID...')).toBeInTheDocument()
  })
})

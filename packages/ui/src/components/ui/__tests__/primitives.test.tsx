import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProviderStateBadge } from '../ProviderStateBadge'
import { MetricCard } from '../MetricCard'
import { ActionButton } from '../ActionButton'
import { HealthBadge } from '../HealthBadge'
import { CircuitBreakerBadge } from '../CircuitBreakerBadge'
import { EmptyState } from '../EmptyState'
import { LoadingSpinner } from '../LoadingSpinner'

// -- ProviderStateBadge --

describe('ProviderStateBadge', () => {
  it.each([
    ['cold', 'Cold'],
    ['ready', 'Ready'],
    ['degraded', 'Degraded'],
    ['dead', 'Dead'],
    ['initializing', 'Initializing'],
  ] as const)('renders "%s" state as "%s"', (state, expectedLabel) => {
    render(<ProviderStateBadge state={state} />)
    expect(screen.getByText(expectedLabel)).toBeInTheDocument()
  })
})

// -- MetricCard --

describe('MetricCard', () => {
  it('renders label and value', () => {
    render(<MetricCard label="Total Providers" value={42} />)
    expect(screen.getByText('Total Providers')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('renders optional subLabel', () => {
    render(<MetricCard label="Errors" value={3} subLabel="last 24h" />)
    expect(screen.getByText('last 24h')).toBeInTheDocument()
  })

  it('does not render subLabel when not provided', () => {
    render(<MetricCard label="Uptime" value="99.9%" />)
    expect(screen.queryByText('last 24h')).not.toBeInTheDocument()
  })
})

// -- ActionButton --

describe('ActionButton', () => {
  it('calls onClick when clicked', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(<ActionButton onClick={onClick}>Start</ActionButton>)

    await user.click(screen.getByRole('button', { name: 'Start' }))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('is disabled when disabled prop is true', () => {
    render(<ActionButton onClick={vi.fn()} disabled>Stop</ActionButton>)
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
  })

  it('is disabled and shows spinner when isLoading', () => {
    render(<ActionButton onClick={vi.fn()} isLoading>Loading</ActionButton>)
    const button = screen.getByRole('button', { name: 'Loading' })
    expect(button).toBeDisabled()
    // Spinner is an SVG rendered inside the button
    expect(button.querySelector('svg')).toBeInTheDocument()
  })

  it('does not fire onClick when disabled', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(<ActionButton onClick={onClick} disabled>Click</ActionButton>)

    await user.click(screen.getByRole('button', { name: 'Click' }))
    expect(onClick).not.toHaveBeenCalled()
  })
})

// -- HealthBadge --

describe('HealthBadge', () => {
  it.each([
    ['healthy', 'Healthy'],
    ['degraded', 'Degraded'],
    ['unhealthy', 'Unhealthy'],
    ['unknown', 'Unknown'],
  ] as const)('renders "%s" status as "%s"', (status, expectedLabel) => {
    render(<HealthBadge status={status} />)
    expect(screen.getByText(expectedLabel)).toBeInTheDocument()
  })
})

// -- CircuitBreakerBadge --

describe('CircuitBreakerBadge', () => {
  it.each([
    ['closed', 'Closed'],
    ['open', 'Open'],
    ['half_open', 'Half-open'],
  ] as const)('renders "%s" state as "%s"', (state, expectedLabel) => {
    render(<CircuitBreakerBadge state={state} />)
    expect(screen.getByText(expectedLabel)).toBeInTheDocument()
  })
})

// -- EmptyState --

describe('EmptyState', () => {
  it('renders default message', () => {
    render(<EmptyState />)
    expect(screen.getByText('No items found.')).toBeInTheDocument()
  })

  it('renders custom message', () => {
    render(<EmptyState message="Nothing here." />)
    expect(screen.getByText('Nothing here.')).toBeInTheDocument()
  })
})

// -- LoadingSpinner --

describe('LoadingSpinner', () => {
  it('renders an SVG element', () => {
    const { container } = render(<LoadingSpinner />)
    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('respects custom size', () => {
    const { container } = render(<LoadingSpinner size={48} />)
    const svg = container.querySelector('svg')
    expect(svg).toHaveAttribute('width', '48')
    expect(svg).toHaveAttribute('height', '48')
  })
})

import { describe, it, expect, vi } from 'vitest'
import { ProviderRow } from '../ProviderRow'
import { renderWithProviders, screen } from '../../../test/render'
import { buildProviderSummary } from '../../../test/fixtures'

describe('ProviderRow', () => {
  const defaultProps = {
    onStart: vi.fn(),
    onStop: vi.fn(),
    isStarting: false,
    isStopping: false,
  }

  function renderRow(overrides: Parameters<typeof buildProviderSummary>[0] = {}) {
    const provider = buildProviderSummary(overrides)
    return renderWithProviders(
      <table>
        <tbody>
          <ProviderRow provider={provider} {...defaultProps} />
        </tbody>
      </table>,
    )
  }

  it('renders provider id as a link', () => {
    renderRow({ provider_id: 'my-provider' })
    const link = screen.getByRole('link', { name: 'my-provider' })
    expect(link).toHaveAttribute('href', '/providers/my-provider')
  })

  it('renders state badge', () => {
    renderRow({ state: 'ready' })
    expect(screen.getByText('Ready')).toBeInTheDocument()
  })

  it('renders mode', () => {
    renderRow({ mode: 'docker' })
    expect(screen.getByText('docker')).toBeInTheDocument()
  })

  it('renders tools count', () => {
    renderRow({ tools_count: 7 })
    expect(screen.getByText('7')).toBeInTheDocument()
  })

  it('disables Start button for ready providers', () => {
    renderRow({ state: 'ready' })
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled()
  })

  it('enables Start button for cold providers', () => {
    renderRow({ state: 'cold' })
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled()
  })

  it('disables Stop button for cold providers', () => {
    renderRow({ state: 'cold' })
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
  })

  it('enables Stop button for ready providers', () => {
    renderRow({ state: 'ready' })
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled()
  })

  it('calls onStart when Start is clicked', async () => {
    const onStart = vi.fn()
    const provider = buildProviderSummary({ provider_id: 'math', state: 'cold' })
    const { user } = renderWithProviders(
      <table>
        <tbody>
          <ProviderRow provider={provider} {...defaultProps} onStart={onStart} />
        </tbody>
      </table>,
    )

    await user.click(screen.getByRole('button', { name: 'Start' }))
    expect(onStart).toHaveBeenCalledWith('math')
  })

  it('calls onStop when Stop is clicked', async () => {
    const onStop = vi.fn()
    const provider = buildProviderSummary({ provider_id: 'search', state: 'ready' })
    const { user } = renderWithProviders(
      <table>
        <tbody>
          <ProviderRow provider={provider} {...defaultProps} onStop={onStop} />
        </tbody>
      </table>,
    )

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).toHaveBeenCalledWith('search')
  })
})

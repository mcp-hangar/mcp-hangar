import { render, renderHook, type RenderHookOptions, type RenderOptions } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, type MemoryRouterProps } from 'react-router'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'

export { screen, waitFor, within } from '@testing-library/react'

interface CustomRenderOptions extends Omit<RenderOptions, 'wrapper'> {
  initialEntries?: MemoryRouterProps['initialEntries']
  queryClient?: QueryClient
}

interface CustomRenderHookOptions<Props>
  extends Omit<RenderHookOptions<Props>, 'wrapper'> {
  initialEntries?: MemoryRouterProps['initialEntries']
  queryClient?: QueryClient
}

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

function createProvidersWrapper(
  initialEntries: MemoryRouterProps['initialEntries'],
  queryClient: QueryClient,
) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <MemoryRouter initialEntries={initialEntries}>
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      </MemoryRouter>
    )
  }
}

/**
 * Render with all required providers: QueryClientProvider + MemoryRouter.
 * Returns the standard render result plus a pre-configured userEvent instance.
 */
export function renderWithProviders(
  ui: ReactElement,
  options: CustomRenderOptions = {},
) {
  const {
    initialEntries = ['/'],
    queryClient = createTestQueryClient(),
    ...renderOptions
  } = options

  const wrapper = createProvidersWrapper(initialEntries, queryClient)

  const result = render(ui, { wrapper, ...renderOptions })
  const user = userEvent.setup()

  return { ...result, user }
}

export function renderHookWithProviders<Result, Props>(
  renderCallback: (initialProps: Props) => Result,
  options: CustomRenderHookOptions<Props> = {},
) {
  const {
    initialEntries = ['/'],
    queryClient = createTestQueryClient(),
    ...renderHookOptions
  } = options

  const wrapper = createProvidersWrapper(initialEntries, queryClient)

  return renderHook(renderCallback, { wrapper, ...renderHookOptions })
}

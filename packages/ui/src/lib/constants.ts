export const ROUTES = {
  DASHBOARD: '/',
  PROVIDERS: '/providers',
  PROVIDER_DETAIL: '/providers/:id',
  GROUPS: '/groups',
  EXECUTIONS: '/executions',
  METRICS: '/metrics',
  EVENTS: '/events',
  DISCOVERY: '/discovery',
  AUTH: '/auth',
  CONFIG: '/config',
} as const

export const NAV_ITEMS = [
  { label: 'Dashboard', path: ROUTES.DASHBOARD, icon: 'layout-dashboard' },
  { label: 'Providers', path: ROUTES.PROVIDERS, icon: 'server' },
  { label: 'Groups', path: ROUTES.GROUPS, icon: 'layers' },
  { label: 'Executions', path: ROUTES.EXECUTIONS, icon: 'activity' },
  { label: 'Metrics', path: ROUTES.METRICS, icon: 'bar-chart-2' },
  { label: 'Events', path: ROUTES.EVENTS, icon: 'radio' },
  { label: 'Discovery', path: ROUTES.DISCOVERY, icon: 'search' },
  { label: 'Auth', path: ROUTES.AUTH, icon: 'shield' },
  { label: 'Config', path: ROUTES.CONFIG, icon: 'settings' },
] as const

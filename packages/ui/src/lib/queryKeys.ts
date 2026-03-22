export const queryKeys = {
  providers: {
    all: ['providers'] as const,
    list: (stateFilter?: string) => ['providers', 'list', stateFilter] as const,
    detail: (id: string) => ['providers', 'detail', id] as const,
    tools: (id: string) => ['providers', 'tools', id] as const,
    health: (id: string) => ['providers', 'health', id] as const,
    toolHistory: (id: string) => ['providers', 'toolHistory', id] as const,
  },
  groups: {
    all: ['groups'] as const,
    list: () => ['groups', 'list'] as const,
    detail: (id: string) => ['groups', 'detail', id] as const,
  },
  discovery: {
    all: ['discovery'] as const,
    sources: () => ['discovery', 'sources'] as const,
    pending: () => ['discovery', 'pending'] as const,
    quarantined: () => ['discovery', 'quarantined'] as const,
  },
  catalog: {
    all: ['catalog'] as const,
    list: (params?: { search?: string; tags?: string }) => ['catalog', 'list', params] as const,
    detail: (entryId: string) => ['catalog', 'detail', entryId] as const,
  },
  config: {
    all: ['config'] as const,
    current: () => ['config', 'current'] as const,
  },
  system: {
    all: ['system'] as const,
    info: () => ['system', 'info'] as const,
    metrics: () => ['system', 'metrics'] as const,
  },
  auth: {
    all: ['auth'] as const,
    apiKeys: (principalId?: string) => ['auth', 'apiKeys', principalId] as const,
    roles: () => ['auth', 'roles'] as const,
    assignments: (principalId?: string) => ['auth', 'assignments', principalId] as const,
  },
  observability: {
    all: ['observability'] as const,
    metrics: () => ['observability', 'metrics'] as const,
    metricsHistory: (params?: object) => ['observability', 'metricsHistory', params] as const,
    audit: (params?: object) => ['observability', 'audit', params] as const,
    securityEvents: () => ['observability', 'securityEvents'] as const,
    alerts: () => ['observability', 'alerts'] as const,
  },
}

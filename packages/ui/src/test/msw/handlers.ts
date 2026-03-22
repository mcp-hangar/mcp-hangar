import { http, HttpResponse } from 'msw'
import {
  buildAlert,
  buildHealthInfo,
  buildProviderDetails,
  buildProviderSummary,
  buildSystemApiResponse,
} from '../fixtures'
import type {
  ProviderCreateRequest,
  ProviderUpdateRequest,
  GroupCreateRequest,
  GroupUpdateRequest,
  GroupMemberAddRequest,
  GroupMemberUpdateRequest,
} from '../../types/provider-crud'
import type { RegisterSourceRequest } from '../../types/system'
import type { AddCatalogEntryRequest } from '../../types/catalog'
import type { CheckPermissionRequest, SetToolAccessPolicyRequest, UpdateRoleRequest } from '../../types/auth'

const BASE = '/api'

// --- Mock data ---

const mockDiscoverySources = [
  {
    source_id: 'docker-local',
    source_type: 'docker',
    mode: 'additive',
    is_healthy: true,
    is_enabled: true,
    last_discovery: '2026-03-22T14:00:00Z',
    providers_count: 3,
    error_message: null,
  },
  {
    source_id: 'k8s-prod',
    source_type: 'kubernetes',
    mode: 'authoritative',
    is_healthy: false,
    is_enabled: true,
    last_discovery: '2026-03-22T13:00:00Z',
    providers_count: 0,
    error_message: 'Connection refused to cluster API',
  },
]

const mockPendingProviders = [
  {
    name: 'auto-detected-math',
    source_type: 'docker',
    mode: 'subprocess',
    connection_info: { command: ['python', '-m', 'math_server'] },
    metadata: { image: 'math-provider:latest' },
    fingerprint: 'abc123',
    discovered_at: '2026-03-22T14:30:00Z',
    last_seen_at: '2026-03-22T14:30:00Z',
    ttl_seconds: 3600,
    is_expired: false,
  },
]

const mockCatalogEntries = [
  {
    entry_id: 'cat-001',
    name: 'filesystem',
    description: 'Read and write files on the local filesystem',
    mode: 'subprocess',
    command: ['uvx', 'mcp-server-filesystem'],
    image: null,
    tags: ['files', 'local'],
    verified: true,
    source: 'builtin',
    required_env: [],
    builtin: true,
  },
  {
    entry_id: 'cat-002',
    name: 'brave-search',
    description: 'Web search powered by Brave Search API',
    mode: 'subprocess',
    command: ['uvx', 'mcp-server-brave-search'],
    image: null,
    tags: ['search', 'web'],
    verified: true,
    source: 'builtin',
    required_env: ['BRAVE_API_KEY'],
    builtin: true,
  },
  {
    entry_id: 'cat-003',
    name: 'custom-analytics',
    description: 'Internal analytics pipeline provider',
    mode: 'docker',
    command: [],
    image: 'registry.internal/analytics-mcp:latest',
    tags: ['analytics', 'internal'],
    verified: false,
    source: 'custom',
    required_env: ['DB_URL'],
    builtin: false,
  },
]

export const handlers = [
  // GET /api/providers/ -- provider list
  http.get(`${BASE}/providers/`, ({ request }) => {
    const url = new URL(request.url)
    const stateFilter = url.searchParams.get('state')

    const providers = [
      buildProviderSummary({ provider_id: 'math', state: 'ready', tools_count: 4 }),
      buildProviderSummary({ provider_id: 'search', state: 'cold', tools_count: 2 }),
      buildProviderSummary({ provider_id: 'cache', state: 'degraded', tools_count: 1 }),
    ]

    const filtered = stateFilter ? providers.filter((p) => p.state === stateFilter) : providers

    return HttpResponse.json({ providers: filtered })
  }),

  // POST /api/providers/ -- create provider
  http.post(`${BASE}/providers/`, async ({ request }) => {
    const body = (await request.json()) as ProviderCreateRequest
    return HttpResponse.json(
      buildProviderSummary({
        provider_id: body.provider_id,
        mode: body.mode,
        state: 'cold',
        tools_count: 0,
      }),
      { status: 201 }
    )
  }),

  // POST /api/providers/:id/start -- start provider
  http.post(`${BASE}/providers/:id/start`, ({ params }) => {
    return HttpResponse.json({ status: 'started', provider_id: params.id })
  }),

  // POST /api/providers/:id/stop -- stop provider
  http.post(`${BASE}/providers/:id/stop`, ({ params }) => {
    return HttpResponse.json({ status: 'stopped', provider_id: params.id })
  }),

  // GET /api/providers/:id -- provider details
  http.get(`${BASE}/providers/:id`, ({ params }) => {
    const providerId = String(params.id)
    return HttpResponse.json(
      buildProviderDetails({
        provider_id: providerId,
        tools: [
          {
            name: `${providerId}-tool`,
            description: `Tool exposed by ${providerId}`,
            inputSchema: { type: 'object' },
          },
        ],
      })
    )
  }),

  // PUT /api/providers/:id -- update provider
  http.put(`${BASE}/providers/:id`, async ({ params, request }) => {
    const body = (await request.json()) as ProviderUpdateRequest
    return HttpResponse.json(
      buildProviderDetails({
        provider_id: String(params.id),
        description: body.description,
      })
    )
  }),

  // DELETE /api/providers/:id -- delete provider
  http.delete(`${BASE}/providers/:id`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // GET /api/providers/:id/health -- provider health details
  http.get(`${BASE}/providers/:id/health`, () => {
    return HttpResponse.json(buildHealthInfo())
  }),

  // GET /api/groups/ -- group list
  http.get(`${BASE}/groups/`, () => {
    return HttpResponse.json({
      groups: [
        {
          group_id: 'primary',
          strategy: 'round_robin',
          healthy_count: 2,
          total_members: 2,
          circuit_open: false,
          state: 'healthy',
          min_healthy: 1,
          is_available: true,
        },
      ],
    })
  }),

  // GET /api/groups/:id -- group detail
  http.get(`${BASE}/groups/:id`, ({ params }) => {
    return HttpResponse.json({
      group_id: String(params.id),
      strategy: 'round_robin',
      healthy_count: 1,
      total_members: 1,
      circuit_open: false,
      state: 'healthy',
      min_healthy: 1,
      is_available: true,
      members: [
        {
          id: 'math',
          state: 'ready',
          in_rotation: true,
          weight: 1,
          priority: 0,
          consecutive_failures: 0,
        },
      ],
    })
  }),

  // POST /api/groups/ -- create group
  http.post(`${BASE}/groups/`, async ({ request }) => {
    const body = (await request.json()) as GroupCreateRequest
    return HttpResponse.json(
      {
        group_id: body.group_id,
        strategy: body.strategy,
        state: 'healthy',
      },
      { status: 201 }
    )
  }),

  // PUT /api/groups/:id -- update group
  http.put(`${BASE}/groups/:id`, async ({ params, request }) => {
    const body = (await request.json()) as GroupUpdateRequest
    return HttpResponse.json({
      group_id: String(params.id),
      strategy: body.strategy ?? 'round_robin',
      state: 'healthy',
    })
  }),

  // DELETE /api/groups/:id -- delete group
  http.delete(`${BASE}/groups/:id`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // POST /api/groups/:groupId/members -- add member
  http.post(`${BASE}/groups/:groupId/members`, async ({ request }) => {
    const body = (await request.json()) as GroupMemberAddRequest
    return HttpResponse.json(
      {
        id: body.provider_id,
        state: 'cold',
        in_rotation: false,
        weight: body.weight ?? 1,
        priority: body.priority ?? 0,
        consecutive_failures: 0,
      },
      { status: 201 }
    )
  }),

  // PUT /api/groups/:groupId/members/:memberId -- update member
  http.put(`${BASE}/groups/:groupId/members/:memberId`, async ({ params, request }) => {
    const body = (await request.json()) as GroupMemberUpdateRequest
    return HttpResponse.json({
      id: String(params.memberId),
      state: 'ready',
      in_rotation: true,
      weight: body.weight ?? 1,
      priority: body.priority ?? 0,
      consecutive_failures: 0,
    })
  }),

  // DELETE /api/groups/:groupId/members/:memberId -- remove member
  http.delete(`${BASE}/groups/:groupId/members/:memberId`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // GET /api/system/ -- system info + metrics
  http.get(`${BASE}/system/`, () => {
    return HttpResponse.json(buildSystemApiResponse())
  }),

  // GET /api/observability/alerts -- alerts
  http.get(`${BASE}/observability/alerts`, () => {
    return HttpResponse.json({
      alerts: [
        buildAlert({ alert_id: 'a-1', level: 'critical', message: 'Provider math is dead', resolved_at: null }),
        buildAlert({
          alert_id: 'a-2',
          level: 'warning',
          message: 'High error rate',
          resolved_at: '2026-03-20T11:00:00Z',
        }),
      ],
    })
  }),

  // --- Discovery source management ---

  // GET /api/discovery/sources -- discovery source list
  http.get(`${BASE}/discovery/sources`, () => {
    return HttpResponse.json({ sources: mockDiscoverySources })
  }),

  // GET /api/discovery/pending -- pending providers
  http.get(`${BASE}/discovery/pending`, () => {
    return HttpResponse.json({ pending: mockPendingProviders })
  }),

  // GET /api/discovery/quarantined -- quarantined providers
  http.get(`${BASE}/discovery/quarantined`, () => {
    return HttpResponse.json({ quarantined: {} })
  }),

  // POST /api/discovery/approve/:name -- approve pending provider
  http.post(`${BASE}/discovery/approve/:name`, ({ params }) => {
    return HttpResponse.json({ status: 'approved', name: params.name })
  }),

  // POST /api/discovery/reject/:name -- reject pending/quarantined provider
  http.post(`${BASE}/discovery/reject/:name`, ({ params }) => {
    return HttpResponse.json({ status: 'rejected', name: params.name })
  }),

  // POST /api/discovery/sources -- register new discovery source
  http.post(`${BASE}/discovery/sources`, async ({ request }) => {
    const body = (await request.json()) as RegisterSourceRequest
    return HttpResponse.json({ source_id: `${body.source_type}-new`, registered: true }, { status: 201 })
  }),

  // PUT /api/discovery/sources/:sourceId -- update discovery source
  http.put(`${BASE}/discovery/sources/:sourceId`, async ({ params }) => {
    return HttpResponse.json({ source_id: String(params.sourceId), updated: true })
  }),

  // DELETE /api/discovery/sources/:sourceId -- deregister discovery source
  http.delete(`${BASE}/discovery/sources/:sourceId`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // POST /api/discovery/sources/:sourceId/scan -- trigger scan
  http.post(`${BASE}/discovery/sources/:sourceId/scan`, ({ params }) => {
    return HttpResponse.json({
      source_id: String(params.sourceId),
      scan_triggered: true,
      providers_found: 2,
    })
  }),

  // PUT /api/discovery/sources/:sourceId/enable -- toggle source enabled/disabled
  http.put(`${BASE}/discovery/sources/:sourceId/enable`, async ({ params, request }) => {
    const body = (await request.json()) as { enabled: boolean }
    return HttpResponse.json({
      source_id: String(params.sourceId),
      enabled: body.enabled,
    })
  }),

  // --- Catalog ---

  // GET /api/catalog/ -- list catalog entries
  http.get(`${BASE}/catalog/`, ({ request }) => {
    const url = new URL(request.url)
    const search = url.searchParams.get('search')?.toLowerCase()
    const tags = url.searchParams.get('tags')

    let entries = [...mockCatalogEntries]
    if (search) {
      entries = entries.filter(
        (e) => e.name.toLowerCase().includes(search) || e.description.toLowerCase().includes(search)
      )
    }
    if (tags) {
      const tagList = tags.split(',')
      entries = entries.filter((e) => e.tags.some((t) => tagList.includes(t)))
    }

    return HttpResponse.json({ entries, total: entries.length })
  }),

  // GET /api/catalog/:entryId -- get single catalog entry
  http.get(`${BASE}/catalog/:entryId`, ({ params }) => {
    const entry = mockCatalogEntries.find((e) => e.entry_id === params.entryId)
    if (!entry) {
      return HttpResponse.json({ error: { code: 'NOT_FOUND', message: 'Entry not found' } }, { status: 404 })
    }
    return HttpResponse.json(entry)
  }),

  // POST /api/catalog/entries -- add custom catalog entry
  http.post(`${BASE}/catalog/entries`, async ({ request }) => {
    const body = (await request.json()) as AddCatalogEntryRequest
    return HttpResponse.json({ entry_id: `cat-new-${body.name}`, added: true }, { status: 201 })
  }),

  // DELETE /api/catalog/entries/:entryId -- remove custom catalog entry
  http.delete(`${BASE}/catalog/entries/:entryId`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // POST /api/catalog/:entryId/deploy -- deploy catalog entry as live provider
  http.post(`${BASE}/catalog/:entryId/deploy`, ({ params }) => {
    const entry = mockCatalogEntries.find((e) => e.entry_id === params.entryId)
    return HttpResponse.json({ provider_id: entry?.name ?? 'deployed-provider', deployed: true }, { status: 201 })
  }),

  // --- Auth: RBAC + Tool Access Policy (Phase 28) ---

  // GET /api/auth/roles/all -- list all roles (builtin + custom)
  http.get(`${BASE}/auth/roles/all`, ({ request }) => {
    const url = new URL(request.url)
    const includeBuiltin = url.searchParams.get('include_builtin') !== 'false'

    const builtinRoles = [
      {
        name: 'admin',
        description: 'Full system access',
        permissions: ['*:*:*'],
        permissions_count: 1,
        is_builtin: true,
      },
      {
        name: 'provider-admin',
        description: 'Manage providers',
        permissions: ['provider:*:*', 'group:*:*'],
        permissions_count: 2,
        is_builtin: true,
      },
      {
        name: 'developer',
        description: 'Invoke tools and read providers',
        permissions: ['tool:invoke:*', 'provider:read:*'],
        permissions_count: 2,
        is_builtin: true,
      },
      {
        name: 'viewer',
        description: 'Read-only access',
        permissions: ['provider:read:*', 'group:read:*', 'config:read:*'],
        permissions_count: 3,
        is_builtin: true,
      },
      {
        name: 'auditor',
        description: 'Audit and security events',
        permissions: ['provider:read:*', 'config:read:*'],
        permissions_count: 2,
        is_builtin: true,
      },
      {
        name: 'service-account',
        description: 'Machine-to-machine access',
        permissions: ['tool:invoke:*'],
        permissions_count: 1,
        is_builtin: true,
      },
    ]

    const customRoles = [
      {
        name: 'custom-operator',
        description: 'Custom operations role',
        permissions: ['provider:start:*', 'provider:stop:*'],
        permissions_count: 2,
        is_builtin: false,
      },
    ]

    const roles = includeBuiltin ? [...builtinRoles, ...customRoles] : customRoles

    return HttpResponse.json({
      roles,
      total: roles.length,
      builtin_count: includeBuiltin ? builtinRoles.length : 0,
      custom_count: customRoles.length,
    })
  }),

  // GET /api/auth/roles/:roleName -- get single role
  http.get(`${BASE}/auth/roles/:roleName`, ({ params }) => {
    const roleName = String(params.roleName)
    // Skip if this matches a known exact path handled elsewhere
    if (roleName === 'all' || roleName === 'assign' || roleName === 'revoke') {
      return
    }
    const allRoles = [
      { name: 'admin', description: 'Full system access', permissions: ['*:*:*'], permissions_count: 1 },
      {
        name: 'custom-operator',
        description: 'Custom operations role',
        permissions: ['provider:start:*', 'provider:stop:*'],
        permissions_count: 2,
      },
    ]
    const role = allRoles.find((r) => r.name === roleName)
    if (!role) {
      return HttpResponse.json({ found: false, role: null })
    }
    return HttpResponse.json({ found: true, role })
  }),

  // DELETE /api/auth/roles/:roleName -- delete custom role
  http.delete(`${BASE}/auth/roles/:roleName`, ({ params }) => {
    const roleName = String(params.roleName)
    if (roleName === 'revoke') {
      return
    }
    return new HttpResponse(null, { status: 204 })
  }),

  // PATCH /api/auth/roles/:roleName -- update custom role
  http.patch(`${BASE}/auth/roles/:roleName`, async ({ params, request }) => {
    const roleName = String(params.roleName)
    const body = (await request.json()) as UpdateRoleRequest
    return HttpResponse.json({
      role_name: roleName,
      description: body.description ?? null,
      permissions_count: body.permissions.length,
      updated: true,
      updated_by: body.updated_by ?? 'system',
    })
  }),

  // GET /api/auth/principals -- list all principals
  http.get(`${BASE}/auth/principals`, () => {
    return HttpResponse.json({
      principals: [
        { principal_id: 'user:alice', roles: ['admin', 'developer'] },
        { principal_id: 'user:bob', roles: ['viewer'] },
        { principal_id: 'service:ci-bot', roles: ['service-account'] },
      ],
      total: 3,
    })
  }),

  // GET /api/auth/permissions -- list all permission resource types
  http.get(`${BASE}/auth/permissions`, () => {
    return HttpResponse.json({
      permissions: [
        { resource_type: 'provider', actions: ['read', 'write', 'invoke', 'admin', 'start', 'stop'] },
        { resource_type: 'group', actions: ['read', 'write', 'admin'] },
        { resource_type: 'tool', actions: ['invoke', 'read'] },
        { resource_type: 'config', actions: ['read', 'write'] },
        { resource_type: '*', actions: ['*'] },
      ],
    })
  }),

  // POST /api/auth/check-permission -- check if principal has permission
  http.post(`${BASE}/auth/check-permission`, async ({ request }) => {
    const body = (await request.json()) as CheckPermissionRequest
    // Mock: admin principal always allowed, others depend on action
    const isAdmin = body.principal_id === 'user:alice'
    return HttpResponse.json({
      principal_id: body.principal_id,
      action: body.action,
      resource_type: body.resource_type,
      resource_id: body.resource_id ?? '*',
      allowed: isAdmin,
      granted_by_role: isAdmin ? 'admin' : null,
    })
  }),

  // GET /api/auth/policies/:scope/:targetId -- get tool access policy
  http.get(`${BASE}/auth/policies/:scope/:targetId`, ({ params }) => {
    const scope = String(params.scope)
    const targetId = String(params.targetId)
    // Return a mock policy for "math" provider, empty for others
    if (targetId === 'math') {
      return HttpResponse.json({
        found: true,
        scope,
        target_id: targetId,
        allow_list: ['math-*', 'calculate'],
        deny_list: ['dangerous-*'],
      })
    }
    return HttpResponse.json({
      found: false,
      scope,
      target_id: targetId,
      allow_list: [],
      deny_list: [],
    })
  }),

  // POST /api/auth/policies/:scope/:targetId -- set tool access policy
  http.post(`${BASE}/auth/policies/:scope/:targetId`, async ({ params, request }) => {
    const scope = String(params.scope)
    const targetId = String(params.targetId)
    const body = (await request.json()) as SetToolAccessPolicyRequest
    return HttpResponse.json({
      scope,
      target_id: targetId,
      allow_list: body.allow_list,
      deny_list: body.deny_list,
      set: true,
    })
  }),

  // DELETE /api/auth/policies/:scope/:targetId -- clear tool access policy
  http.delete(`${BASE}/auth/policies/:scope/:targetId`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // ---- Config endpoints ----

  // GET /api/config -- current config
  http.get(`${BASE}/config`, () => {
    return HttpResponse.json({
      config: {
        providers: {
          math: { mode: 'subprocess', command: ['python', '-m', 'math_server'], idle_ttl_s: 300 },
          weather: { mode: 'remote', url: 'http://weather:8080/mcp' },
        },
        logging: { level: 'INFO', json_format: true },
      },
    })
  }),

  // POST /api/config/reload -- hot reload
  http.post(`${BASE}/config/reload`, () => {
    return HttpResponse.json({
      status: 'reloaded',
      result: null,
      message: 'Config reloaded successfully.',
    })
  }),

  // POST /api/config/export -- serialize in-memory config to YAML
  http.post(`${BASE}/config/export`, () => {
    return HttpResponse.json({
      yaml: 'providers:\n  math:\n    command:\n    - python\n    - -m\n    - math_server\n    idle_ttl_s: 300\n    mode: subprocess\n  weather:\n    mode: remote\n    url: http://weather:8080/mcp\n',
    })
  }),

  // POST /api/config/backup -- create rotating backup
  http.post(`${BASE}/config/backup`, () => {
    return HttpResponse.json({
      path: '/etc/mcp-hangar/config.yaml.bak1',
    })
  }),

  // GET /api/config/diff -- unified diff between on-disk and in-memory
  http.get(`${BASE}/config/diff`, () => {
    return HttpResponse.json({
      has_diff: true,
      diff: '--- on-disk\n+++ in-memory\n@@ -1,4 +1,5 @@\n providers:\n   math:\n     command:\n     - python\n     - -m\n     - math_server\n-    idle_ttl_s: 600\n+    idle_ttl_s: 300\n     mode: subprocess\n+  weather:\n+    mode: remote\n+    url: http://weather:8080/mcp\n',
      on_disk: {
        providers: {
          math: { mode: 'subprocess', command: ['python', '-m', 'math_server'], idle_ttl_s: 600 },
        },
      },
      in_memory: {
        providers: {
          math: { mode: 'subprocess', command: ['python', '-m', 'math_server'], idle_ttl_s: 300 },
          weather: { mode: 'remote', url: 'http://weather:8080/mcp' },
        },
      },
    })
  }),
]

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
]

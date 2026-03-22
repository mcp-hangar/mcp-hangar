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

const BASE = '/api'

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

  // POST /api/providers/:id/start/ -- start provider
  http.post(`${BASE}/providers/:id/start/`, ({ params }) => {
    return HttpResponse.json({ status: 'started', provider_id: params.id })
  }),

  // POST /api/providers/:id/stop/ -- stop provider
  http.post(`${BASE}/providers/:id/stop/`, ({ params }) => {
    return HttpResponse.json({ status: 'stopped', provider_id: params.id })
  }),

  // GET /api/providers/:id/ -- provider details
  http.get(`${BASE}/providers/:id/`, ({ params }) => {
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

  // PUT /api/providers/:id/ -- update provider
  http.put(`${BASE}/providers/:id/`, async ({ params, request }) => {
    const body = (await request.json()) as ProviderUpdateRequest
    return HttpResponse.json(
      buildProviderDetails({
        provider_id: String(params.id),
        description: body.description,
      })
    )
  }),

  // DELETE /api/providers/:id/ -- delete provider
  http.delete(`${BASE}/providers/:id/`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // GET /api/providers/:id/health/ -- provider health details
  http.get(`${BASE}/providers/:id/health/`, () => {
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

  // GET /api/groups/:id/ -- group detail
  http.get(`${BASE}/groups/:id/`, ({ params }) => {
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

  // PUT /api/groups/:id/ -- update group
  http.put(`${BASE}/groups/:id/`, async ({ params, request }) => {
    const body = (await request.json()) as GroupUpdateRequest
    return HttpResponse.json({
      group_id: String(params.id),
      strategy: body.strategy ?? 'round_robin',
      state: 'healthy',
    })
  }),

  // DELETE /api/groups/:id/ -- delete group
  http.delete(`${BASE}/groups/:id/`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // POST /api/groups/:groupId/members/ -- add member
  http.post(`${BASE}/groups/:groupId/members/`, async ({ request }) => {
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

  // PUT /api/groups/:groupId/members/:memberId/ -- update member
  http.put(`${BASE}/groups/:groupId/members/:memberId/`, async ({ params, request }) => {
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

  // DELETE /api/groups/:groupId/members/:memberId/ -- remove member
  http.delete(`${BASE}/groups/:groupId/members/:memberId/`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // GET /api/system/ -- system info + metrics
  http.get(`${BASE}/system/`, () => {
    return HttpResponse.json(buildSystemApiResponse())
  }),

  // GET /api/observability/alerts/ -- alerts
  http.get(`${BASE}/observability/alerts/`, () => {
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
]

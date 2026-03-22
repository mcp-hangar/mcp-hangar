import type { DomainEvent } from '../types/events'
import type {
  HealthInfo,
  ProviderDetails,
  ProviderSummary,
  ProviderState,
  ProviderMode,
  ToolInfo,
} from '../types/provider'
import type { SystemMetrics } from '../types/system'
import type { Alert } from '../types/metrics'
import type { LogLine } from '../hooks/useProviderLogs'

let alertCounter = 0

export function buildProviderSummary(
  overrides: Partial<ProviderSummary> = {},
): ProviderSummary {
  return {
    provider_id: 'test-provider',
    state: 'ready' as ProviderState,
    mode: 'subprocess' as ProviderMode,
    alive: true,
    tools_count: 3,
    health_status: 'healthy',
    health: { status: 'healthy' },
    ...overrides,
  }
}

export function buildToolInfo(overrides: Partial<ToolInfo> = {}): ToolInfo {
  return {
    name: 'sum',
    description: 'Adds numbers',
    inputSchema: {
      type: 'object',
      properties: {
        a: { type: 'number' },
        b: { type: 'number' },
      },
      required: ['a', 'b'],
    },
    ...overrides,
  }
}

export function buildHealthInfo(
  overrides: Partial<HealthInfo> = {},
): HealthInfo {
  return {
    status: 'healthy',
    consecutive_failures: 0,
    total_invocations: 128,
    total_failures: 2,
    success_rate: 0.984,
    can_retry: true,
    last_success_ago: 15,
    last_failure_ago: null,
    ...overrides,
  }
}

export function buildProviderDetails(
  overrides: Partial<ProviderDetails> = {},
): ProviderDetails {
  return {
    ...buildProviderSummary({
      provider_id: 'math',
      tools_count: undefined,
    }),
    tools: [buildToolInfo()],
    health: buildHealthInfo(),
    idle_time: 12,
    idle_ttl_s: 120,
    command: ['python', '-m', 'math_provider'],
    circuit_breaker: {
      state: 'closed',
      failure_count: 0,
      opened_at: null,
    },
    meta: {},
    ...overrides,
  }
}

export function buildSystemMetrics(
  overrides: Partial<SystemMetrics> = {},
): SystemMetrics {
  return {
    total_providers: 5,
    providers_by_state: { ready: 3, cold: 1, degraded: 1 },
    total_tool_calls: 142,
    error_rate: 0.02,
    ...overrides,
  }
}

export function buildAlert(overrides: Partial<Alert> = {}): Alert {
  alertCounter += 1
  return {
    alert_id: `alert-${alertCounter}`,
    level: 'warning',
    message: 'Provider degraded',
    provider_id: 'test-provider',
    event_type: 'ProviderDegraded',
    timestamp: '2026-03-20T10:00:00Z',
    created_at: '2026-03-20T10:00:00Z',
    resolved_at: null,
    ...overrides,
  }
}

export function buildLogLine(overrides: Partial<LogLine> = {}): LogLine {
  return {
    provider_id: 'math',
    stream: 'stdout',
    content: 'provider ready',
    recorded_at: Date.parse('2026-03-20T10:00:00Z'),
    ...overrides,
  }
}

export function buildDomainEvent(
  overrides: Partial<DomainEvent> = {},
): DomainEvent {
  return {
    event_id: 'evt-1',
    event_type: 'ProviderStateChanged',
    occurred_at: '2026-03-20T10:00:00Z',
    provider_id: 'math',
    ...overrides,
  }
}

export function buildSystemApiResponse(metricsOverrides: Partial<SystemMetrics> = {}) {
  const m = buildSystemMetrics(metricsOverrides)
  return {
    system: {
      total_providers: m.total_providers,
      providers_by_state: m.providers_by_state,
      total_tools: 10,
      total_invocations: m.total_tool_calls,
      total_failures: m.error_rate != null ? Math.round(m.total_tool_calls * m.error_rate) : 0,
      overall_success_rate: m.error_rate != null ? 1 - m.error_rate : 1,
      uptime_seconds: 3600,
      version: '2.0.0',
    },
  }
}

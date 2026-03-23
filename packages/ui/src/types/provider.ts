export type ProviderState = 'cold' | 'initializing' | 'ready' | 'degraded' | 'dead'
export type ProviderMode = 'subprocess' | 'docker' | 'remote'

export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
}

export interface CircuitBreakerStatus {
  state: 'closed' | 'open' | 'half_open'
}

export interface CircuitBreakerInfo extends CircuitBreakerStatus {
  failure_count: number
  opened_at?: string | null
}

export interface ToolSchema {
  type: string
  properties?: Record<string, unknown>
  required?: string[]
  [key: string]: unknown
}

export interface ToolInfo {
  name: string
  description?: string
  inputSchema?: ToolSchema
  outputSchema?: ToolSchema
}

/**
 * Health info as returned by GET /providers/{id}/health.
 * Flat structure matching HealthInfo.to_dict() in provider_views.py.
 */
export interface HealthInfo {
  status: HealthStatus['status']
  consecutive_failures: number
  total_invocations: number
  total_failures: number
  success_rate: number
  can_retry: boolean
  last_success_ago: number | null
  last_failure_ago: number | null
}

/**
 * Provider summary as returned by GET /providers/.
 * Matches ProviderSummary.to_dict() in provider_views.py.
 */
export interface ProviderSummary {
  provider_id: string
  state: ProviderState
  mode: ProviderMode
  alive: boolean
  tools_count: number
  health_status: string
  health?: HealthStatus
  tools_predefined?: boolean
  description?: string
}

/**
 * Provider details as returned by GET /providers/{id}.
 * Matches ProviderDetails.to_dict() in provider_views.py.
 */
export interface ProviderDetails extends Omit<ProviderSummary, 'tools_count'> {
  tools: ToolInfo[]
  health: HealthInfo
  idle_time: number
  idle_ttl_s?: number | null
  command?: string[]
  circuit_breaker?: CircuitBreakerInfo | null
  meta: Record<string, unknown>
}

/**
 * Raw event store record as returned by GET /providers/{id}/tools/history.
 * Each record is a raw event from the event store.
 */
export interface ToolInvocationRecord {
  stream_id: string
  version: number
  event_type: string
  event_id: string
  occurred_at: number
  data: Record<string, unknown>
  stored_at: number
}

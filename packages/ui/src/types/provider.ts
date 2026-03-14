export type ProviderState = 'cold' | 'initializing' | 'ready' | 'degraded' | 'dead'
export type ProviderMode = 'subprocess' | 'docker' | 'remote'

export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
  last_check?: string
  consecutive_failures: number
  message?: string
}

export interface CircuitBreakerStatus {
  state: 'closed' | 'open' | 'half_open'
  failure_count: number
  opened_at?: string
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
  schema?: ToolSchema
}

export interface ProviderSummary {
  provider_id: string
  state: ProviderState
  mode: ProviderMode
  tools_count: number
  idle_ttl_s?: number
  health?: HealthStatus
  last_started?: string
  last_stopped?: string
}

export interface ProviderDetails extends ProviderSummary {
  command?: string[]
  env?: Record<string, string>
  tools: ToolInfo[]
  circuit_breaker?: CircuitBreakerStatus
  config?: Record<string, unknown>
}

export interface HealthInfo {
  provider_id: string
  status: HealthStatus
  history?: HealthStatus[]
}

export interface ToolInvocationRecord {
  correlation_id: string
  provider_id: string
  tool_name: string
  requested_at: string
  completed_at?: string
  duration_ms?: number
  success?: boolean
  error?: string
}

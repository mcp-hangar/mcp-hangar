export interface ProviderMetrics {
  provider_id: string
  tool_calls_total: number
  tool_call_errors: number
  tool_call_duration_p50_ms?: number
  tool_call_duration_p95_ms?: number
  cold_starts_total: number
  health_checks_total: number
  health_check_failures: number
}

export interface MetricsSnapshot {
  timestamp: string
  providers: ProviderMetrics[]
  system?: Record<string, unknown>
}

export interface AuditEntry {
  id: string
  entity_id: string
  entity_type: string
  action: string
  actor?: string
  occurred_at: string
  details?: Record<string, unknown>
}

export interface SecurityEvent {
  event_id: string
  event_type: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  occurred_at: string
  details?: Record<string, unknown>
}

export interface Alert {
  alert_id: string
  level: 'warning' | 'critical'
  message: string
  created_at: string
  resolved_at?: string
}

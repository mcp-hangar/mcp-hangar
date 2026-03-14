export interface ProviderMetrics {
  provider_id: string
  tool_calls_total: number
  tool_call_errors: number
  cold_starts_total: number
  health_checks_total: number
  health_check_failures: number
}

export interface PerProviderMetricsResponse {
  providers: ProviderMetrics[]
  timestamp: string
}

export interface AuditRecord {
  event_id: string
  event_type: string
  occurred_at: string
  provider_id?: string
  data?: Record<string, unknown>
  recorded_at?: string
}

export interface SecurityEvent {
  event_id: string
  event_type: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  message: string
  timestamp: string
  provider_id?: string
  tool_name?: string
  source_ip?: string | null
  user_id?: string | null
  details?: Record<string, unknown>
  correlation_id?: string | null
}

export interface Alert {
  alert_id: string
  level: 'warning' | 'critical'
  message: string
  created_at: string
  resolved_at?: string
}

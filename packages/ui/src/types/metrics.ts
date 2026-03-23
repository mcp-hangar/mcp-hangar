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

/**
 * Security event as returned by GET /observability/security.
 * Matches SecurityEvent.to_dict() in security_handler.py.
 * severity can be 'info' | 'low' | 'medium' | 'high' | 'critical'.
 */
export interface SecurityEvent {
  event_id: string
  event_type: string
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical'
  message: string
  timestamp: string
  provider_id?: string | null
  tool_name?: string | null
  source_ip?: string | null
  user_id?: string | null
  details?: Record<string, unknown>
  correlation_id?: string | null
}

/**
 * Alert as returned by GET /observability/alerts.
 * Matches Alert.to_dict() in alert_handler.py.
 */
export interface Alert {
  alert_id: string
  level: 'warning' | 'critical' | 'info'
  message: string
  provider_id: string
  event_type: string
  timestamp: string
  created_at: string
  resolved_at?: string | null
  details?: Record<string, unknown>
}

export interface MetricHistoryPoint {
  provider_id: string
  metric_name: string
  value: number
  recorded_at: number
}

export interface MetricsHistoryResponse {
  points: MetricHistoryPoint[]
  count: number
}

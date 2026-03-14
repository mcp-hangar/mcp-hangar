import type { Alert, AuditEntry, SecurityEvent } from '../types/metrics'
import { apiClient } from './client'

export interface AuditQueryParams {
  entity_id?: string
  since?: string
  until?: string
  limit?: number
}

export const eventsApi = {
  audit: (params?: AuditQueryParams) => {
    const qs = new URLSearchParams()
    if (params?.entity_id) qs.set('entity_id', params.entity_id)
    if (params?.since) qs.set('since', params.since)
    if (params?.until) qs.set('until', params.until)
    if (params?.limit) qs.set('limit', String(params.limit))
    const query = qs.toString()
    return apiClient.get<{ entries: AuditEntry[] }>(`/observability/audit${query ? `?${query}` : ''}`)
  },
  securityEvents: () =>
    apiClient.get<{ events: SecurityEvent[] }>('/observability/security/events'),
  alerts: () =>
    apiClient.get<{ alerts: Alert[] }>('/observability/alerts'),
}

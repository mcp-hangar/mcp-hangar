import type { Alert, AuditRecord, SecurityEvent } from '../types/metrics'
import { apiClient } from './client'

export interface AuditQueryParams {
  provider_id?: string
  event_type?: string
  limit?: number
}

export const eventsApi = {
  audit: (params?: AuditQueryParams) => {
    const qs = new URLSearchParams()
    if (params?.provider_id) qs.set('provider_id', params.provider_id)
    if (params?.event_type) qs.set('event_type', params.event_type)
    if (params?.limit) qs.set('limit', String(params.limit))
    const query = qs.toString()
    return apiClient.get<{ records: AuditRecord[]; total: number }>(
      `/observability/audit${query ? `?${query}` : ''}`,
    )
  },
  securityEvents: (params?: { limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.limit) qs.set('limit', String(params.limit))
    const query = qs.toString()
    return apiClient.get<{ events: SecurityEvent[]; total: number }>(
      `/observability/security${query ? `?${query}` : ''}`,
    )
  },
  alerts: () => apiClient.get<{ alerts: Alert[] }>('/observability/alerts'),
}

import type { MetricsHistoryResponse, PerProviderMetricsResponse } from '../types/metrics'
import { apiClient } from './client'

export interface MetricsHistoryParams {
  provider?: string
  metric?: string
  from?: number
  to?: number
}

export const metricsApi = {
  snapshot: () => apiClient.get<PerProviderMetricsResponse>('/observability/metrics/per-provider'),
  history: (params: MetricsHistoryParams = {}) => {
    const query = new URLSearchParams()
    if (params.provider) query.set('provider', params.provider)
    if (params.metric) query.set('metric', params.metric)
    if (params.from !== undefined) query.set('from', String(params.from))
    if (params.to !== undefined) query.set('to', String(params.to))
    const qs = query.toString()
    return apiClient.get<MetricsHistoryResponse>(`/observability/metrics/history${qs ? `?${qs}` : ''}`)
  },
}

import type { MetricsSnapshot } from '../types/metrics'
import { apiClient } from './client'

export const metricsApi = {
  snapshot: () => apiClient.get<MetricsSnapshot>('/observability/metrics'),
}

import type { SystemInfo, SystemMetrics } from '../types/system'
import { apiClient } from './client'

export const systemApi = {
  info: () => apiClient.get<SystemInfo>('/system/info'),
  metrics: () => apiClient.get<SystemMetrics>('/system/metrics'),
}

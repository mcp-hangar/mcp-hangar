import { apiClient } from './client'

export const configApi = {
  current: () => apiClient.get<{ config: Record<string, unknown> }>('/config/current'),
  reload: () => apiClient.post<{ status: string; message: string }>('/config/reload'),
}

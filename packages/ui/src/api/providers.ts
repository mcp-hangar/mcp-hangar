import type { HealthInfo, ProviderDetails, ProviderSummary, ToolInfo, ToolInvocationRecord } from '../types/provider'
import type { ProviderCreateRequest, ProviderUpdateRequest, ProviderCreateResponse } from '../types/provider-crud'
import type { LogLine } from '../hooks/useProviderLogs'
import { apiClient } from './client'

export const providersApi = {
  list: (stateFilter?: string) =>
    apiClient.get<{ providers: ProviderSummary[] }>(`/providers${stateFilter ? `?state=${stateFilter}` : ''}`),
  get: (id: string) => apiClient.get<ProviderDetails>(`/providers/${id}`),
  create: (req: ProviderCreateRequest) => apiClient.post<ProviderCreateResponse>('/providers', req),
  update: (id: string, req: ProviderUpdateRequest) => apiClient.put<ProviderDetails>(`/providers/${id}`, req),
  delete: (id: string) => apiClient.delete<void>(`/providers/${id}`),
  start: (id: string) => apiClient.post<{ status: string; provider_id: string }>(`/providers/${id}/start`),
  stop: (id: string, reason?: string) =>
    apiClient.post<{ status: string; provider_id: string }>(`/providers/${id}/stop`, { reason }),
  tools: (id: string) => apiClient.get<{ tools: ToolInfo[] }>(`/providers/${id}/tools`),
  health: (id: string) => apiClient.get<HealthInfo>(`/providers/${id}/health`),
  toolHistory: (id: string) => apiClient.get<{ history: ToolInvocationRecord[] }>(`/providers/${id}/tools/history`),
  logs: (id: string, lines = 100) =>
    apiClient.get<{ logs: LogLine[]; provider_id: string; count: number }>(`/providers/${id}/logs?lines=${lines}`),
}

import type { HealthInfo, ProviderDetails, ProviderSummary, ToolInfo, ToolInvocationRecord } from '../types/provider'
import { apiClient } from './client'

export const providersApi = {
  list: (stateFilter?: string) =>
    apiClient.get<{ providers: ProviderSummary[] }>(`/providers${stateFilter ? `?state=${stateFilter}` : ''}`),
  get: (id: string) =>
    apiClient.get<ProviderDetails>(`/providers/${id}`),
  start: (id: string) =>
    apiClient.post<{ status: string; provider_id: string }>(`/providers/${id}/start`),
  stop: (id: string, reason?: string) =>
    apiClient.post<{ status: string; provider_id: string }>(`/providers/${id}/stop`, { reason }),
  tools: (id: string) =>
    apiClient.get<{ tools: ToolInfo[] }>(`/providers/${id}/tools`),
  health: (id: string) =>
    apiClient.get<HealthInfo>(`/providers/${id}/health`),
  toolHistory: (id: string) =>
    apiClient.get<{ history: ToolInvocationRecord[] }>(`/providers/${id}/tools/history`),
}

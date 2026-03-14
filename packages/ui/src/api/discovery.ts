import type { DiscoverySourceStatus, PendingProvider, QuarantinedProvider } from '../types/system'
import { apiClient } from './client'

export const discoveryApi = {
  sources: () => apiClient.get<{ sources: DiscoverySourceStatus[] }>('/discovery/sources'),
  pending: () => apiClient.get<{ pending: PendingProvider[] }>('/discovery/pending'),
  quarantined: () => apiClient.get<{ quarantined: QuarantinedProvider[] }>('/discovery/quarantined'),
  approve: (id: string) => apiClient.post<{ status: string }>(`/discovery/pending/${id}/approve`),
  reject: (id: string) => apiClient.post<{ status: string }>(`/discovery/pending/${id}/reject`),
}

import type {
  DiscoverySourceStatus,
  PendingProvider,
  QuarantinedProvider,
  RegisterSourceRequest,
  UpdateSourceRequest,
} from '../types/system'
import { apiClient } from './client'

/** Raw quarantined response shape from the backend (dict keyed by provider name). */
type QuarantinedRaw = Record<
  string,
  {
    provider: Record<string, unknown>
    reason: string
    quarantine_time: string
  }
>

/** Normalise the backend dict into a flat array of QuarantinedProvider. */
function normaliseQuarantined(raw: QuarantinedRaw): QuarantinedProvider[] {
  return Object.entries(raw).map(([name, entry]) => ({
    name,
    provider: entry.provider as unknown as PendingProvider,
    reason: entry.reason,
    quarantine_time: entry.quarantine_time,
  }))
}

export const discoveryApi = {
  // Read-only queries
  sources: () => apiClient.get<{ sources: DiscoverySourceStatus[] }>('/discovery/sources'),
  pending: () => apiClient.get<{ pending: PendingProvider[] }>('/discovery/pending'),
  quarantined: () =>
    apiClient
      .get<{ quarantined: QuarantinedRaw }>('/discovery/quarantined')
      .then((res) => ({ quarantined: normaliseQuarantined(res.quarantined) })),

  // Provider approval / rejection
  approve: (name: string) => apiClient.post<{ status: string }>(`/discovery/approve/${name}`),
  reject: (name: string) => apiClient.post<{ status: string }>(`/discovery/reject/${name}`),

  // Source management
  registerSource: (body: RegisterSourceRequest) =>
    apiClient.post<{ source_id: string; registered: boolean }>('/discovery/sources', body),

  updateSource: (sourceId: string, body: UpdateSourceRequest) =>
    apiClient.put<{ source_id: string; updated: boolean }>(`/discovery/sources/${sourceId}`, body),

  deregisterSource: (sourceId: string) =>
    apiClient.delete<{ source_id: string; deregistered: boolean }>(`/discovery/sources/${sourceId}`),

  triggerScan: (sourceId: string) =>
    apiClient.post<{ source_id: string; scan_triggered: boolean; providers_found: number }>(
      `/discovery/sources/${sourceId}/scan`
    ),

  toggleSource: (sourceId: string, enabled: boolean) =>
    apiClient.put<{ source_id: string; enabled: boolean }>(`/discovery/sources/${sourceId}/enable`, { enabled }),
}

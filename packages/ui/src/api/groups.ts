import type { GroupDetails, GroupSummary } from '../types/system'
import { apiClient } from './client'

export const groupsApi = {
  list: () => apiClient.get<{ groups: GroupSummary[] }>('/groups'),
  get: (id: string) => apiClient.get<GroupDetails>(`/groups/${id}`),
  rebalance: (id: string) => apiClient.post<{ status: string }>(`/groups/${id}/rebalance`),
}

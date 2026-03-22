import type { GroupDetails, GroupSummary, GroupMember } from '../types/system'
import type {
  GroupCreateRequest,
  GroupUpdateRequest,
  GroupCreateResponse,
  GroupMemberAddRequest,
  GroupMemberUpdateRequest,
} from '../types/provider-crud'
import { apiClient } from './client'

export const groupsApi = {
  list: () => apiClient.get<{ groups: GroupSummary[] }>('/groups'),
  get: (id: string) => apiClient.get<GroupDetails>(`/groups/${id}`),
  rebalance: (id: string) => apiClient.post<{ status: string }>(`/groups/${id}/rebalance`),
  create: (req: GroupCreateRequest) => apiClient.post<GroupCreateResponse>('/groups', req),
  update: (id: string, req: GroupUpdateRequest) => apiClient.put<GroupCreateResponse>(`/groups/${id}`, req),
  delete: (id: string) => apiClient.delete<void>(`/groups/${id}`),
  addMember: (groupId: string, req: GroupMemberAddRequest) =>
    apiClient.post<GroupMember>(`/groups/${groupId}/members`, req),
  removeMember: (groupId: string, memberId: string) => apiClient.delete<void>(`/groups/${groupId}/members/${memberId}`),
  updateMember: (groupId: string, memberId: string, req: GroupMemberUpdateRequest) =>
    apiClient.put<GroupMember>(`/groups/${groupId}/members/${memberId}`, req),
}

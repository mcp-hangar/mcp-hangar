import type { ApiKey, CreateApiKeyRequest, Role, RoleAssignment } from '../types/auth'
import { apiClient } from './client'

export const authApi = {
  listApiKeys: (principalId?: string) =>
    apiClient.get<{ api_keys: ApiKey[] }>(`/auth/api-keys${principalId ? `?principal_id=${principalId}` : ''}`),
  createApiKey: (req: CreateApiKeyRequest) =>
    apiClient.post<ApiKey>('/auth/api-keys', req),
  revokeApiKey: (keyId: string) =>
    apiClient.delete<{ status: string }>(`/auth/api-keys/${keyId}`),
  listBuiltinRoles: () =>
    apiClient.get<{ roles: Role[] }>('/auth/roles/builtin'),
  listAssignments: (principalId?: string) =>
    apiClient.get<{ assignments: RoleAssignment[] }>(
      `/auth/roles/assignments${principalId ? `?principal_id=${principalId}` : ''}`,
    ),
  assignRole: (principalId: string, roleId: string, scope?: string) =>
    apiClient.post<RoleAssignment>('/auth/roles/assignments', { principal_id: principalId, role_id: roleId, scope }),
  revokeRole: (principalId: string, roleId: string) =>
    apiClient.delete<{ status: string }>('/auth/roles/assignments', { principal_id: principalId, role_id: roleId }),
}

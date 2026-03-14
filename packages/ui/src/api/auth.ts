import type {
  ApiKey,
  AssignRoleRequest,
  CreateApiKeyRequest,
  CreateCustomRoleRequest,
  NewApiKeyResponse,
  Role,
  RoleAssignment,
} from '../types/auth'
import { apiClient } from './client'

export const authApi = {
  listApiKeys: (principalId?: string) =>
    apiClient.get<{ principal_id: string; keys: ApiKey[]; total: number; active: number }>(
      `/auth/keys${principalId ? `?principal_id=${principalId}` : ''}`,
    ),

  createApiKey: (req: CreateApiKeyRequest) => apiClient.post<NewApiKeyResponse>('/auth/keys', req),

  revokeApiKey: (keyId: string, reason?: string) =>
    apiClient.delete<{ status: string }>(`/auth/keys/${keyId}`, reason ? { reason } : undefined),

  listRoles: () => apiClient.get<{ roles: Role[] }>('/auth/roles'),

  createCustomRole: (req: CreateCustomRoleRequest) => apiClient.post<Role>('/auth/roles', req),

  assignRole: (req: AssignRoleRequest) =>
    apiClient.post<{ status: string }>('/auth/roles/assign', req),

  revokeRole: (principalId: string, roleName: string, scope?: string) =>
    apiClient.delete<{ status: string }>('/auth/roles/revoke', {
      principal_id: principalId,
      role_name: roleName,
      ...(scope ? { scope } : {}),
    }),

  getPrincipalRoles: (principalId: string, scope?: string) =>
    apiClient.get<{ principal_id: string; roles: RoleAssignment[] }>(
      `/auth/principals/roles?principal_id=${principalId}${scope ? `&scope=${scope}` : ''}`,
    ),
}

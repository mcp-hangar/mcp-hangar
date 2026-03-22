import type {
  AllRolesResponse,
  AllRole,
  AssignRoleRequest,
  CheckPermissionRequest,
  CheckPermissionResponse,
  CreateApiKeyRequest,
  CreateCustomRoleRequest,
  CreatedRoleResponse,
  NewApiKeyResponse,
  PermissionResource,
  PrincipalsResponse,
  Role,
  RoleAssignment,
  SetToolAccessPolicyRequest,
  ToolAccessPolicy,
  ToolAccessPolicyResponse,
  UpdateRoleRequest,
  UpdateRoleResponse,
} from '../types/auth'
import { apiClient } from './client'

export const authApi = {
  // ---- API Key management (existing) ----------------------------------------

  listApiKeys: (principalId?: string) =>
    apiClient.get<{ principal_id: string; keys: import('../types/auth').ApiKey[]; total: number; active: number }>(
      `/auth/keys${principalId ? `?principal_id=${principalId}` : ''}`
    ),

  createApiKey: (req: CreateApiKeyRequest) => apiClient.post<NewApiKeyResponse>('/auth/keys', req),

  revokeApiKey: (keyId: string, reason?: string) =>
    apiClient.delete<{ status: string }>(`/auth/keys/${keyId}`, reason ? { reason } : undefined),

  // ---- Role management (existing) -------------------------------------------

  listRoles: () => apiClient.get<{ roles: Role[] }>('/auth/roles'),

  createCustomRole: (req: CreateCustomRoleRequest) => apiClient.post<CreatedRoleResponse>('/auth/roles', req),

  assignRole: (req: AssignRoleRequest) => apiClient.post<{ status: string }>('/auth/roles/assign', req),

  revokeRole: (principalId: string, roleName: string, scope?: string) =>
    apiClient.delete<{ status: string }>('/auth/roles/revoke', {
      principal_id: principalId,
      role_name: roleName,
      ...(scope ? { scope } : {}),
    }),

  getPrincipalRoles: (principalId: string, scope?: string) =>
    apiClient.get<{ principal_id: string; roles: RoleAssignment[] }>(
      `/auth/principals/roles?principal_id=${principalId}${scope ? `&scope=${scope}` : ''}`
    ),

  // ---- Phase 28: RBAC endpoints (new) --------------------------------------

  /** List all roles (builtin + custom) with full details. */
  listAllRoles: (includeBuiltin?: boolean) =>
    apiClient.get<AllRolesResponse>(`/auth/roles/all${includeBuiltin === false ? '?include_builtin=false' : ''}`),

  /** Get a specific role by name. */
  getRole: (roleName: string) => apiClient.get<{ found: boolean; role: AllRole | null }>(`/auth/roles/${roleName}`),

  /** Delete a custom role (403 for builtin). */
  deleteRole: (roleName: string) => apiClient.delete<void>(`/auth/roles/${roleName}`),

  /** Update a custom role's permissions and description. */
  updateRole: (roleName: string, req: UpdateRoleRequest) =>
    apiClient.patch<UpdateRoleResponse>(`/auth/roles/${roleName}`, req),

  /** List all principals with at least one role assignment. */
  listPrincipals: () => apiClient.get<PrincipalsResponse>('/auth/principals'),

  /** List all available permission resource types and actions. */
  listPermissions: () => apiClient.get<{ permissions: PermissionResource[] }>('/auth/permissions'),

  /** Check if a principal has a specific permission. */
  checkPermission: (req: CheckPermissionRequest) =>
    apiClient.post<CheckPermissionResponse>('/auth/check-permission', req),

  // ---- Phase 28: Tool Access Policy endpoints (new) -------------------------

  /** Get tool access policy for a scope/target. */
  getToolAccessPolicy: (scope: string, targetId: string) =>
    apiClient.get<ToolAccessPolicyResponse>(`/auth/policies/${scope}/${targetId}`),

  /** Set (upsert) tool access policy for a scope/target. */
  setToolAccessPolicy: (scope: string, targetId: string, req: SetToolAccessPolicyRequest) =>
    apiClient.post<ToolAccessPolicy & { set: boolean }>(`/auth/policies/${scope}/${targetId}`, req),

  /** Clear (remove) tool access policy for a scope/target. */
  clearToolAccessPolicy: (scope: string, targetId: string) =>
    apiClient.delete<void>(`/auth/policies/${scope}/${targetId}`),
}

export interface ApiKey {
  key_id: string
  principal_id: string
  name?: string
  created_at: string
  expires_at?: string
  last_used_at?: string
  revoked?: boolean
}

export interface NewApiKeyResponse {
  key_id: string
  raw_key: string
  principal_id: string
  name?: string
}

export interface CreateApiKeyRequest {
  principal_id: string
  name: string
  expires_at?: string
}

/**
 * Role as returned by GET /auth/roles (list of built-in roles).
 * Matches ListBuiltinRolesHandler response: { name, description, permissions_count }.
 * For custom roles created via POST /auth/roles, permissions_count reflects the set size.
 */
export interface Role {
  name: string
  description?: string
  permissions_count: number
}

export interface RoleAssignment {
  principal_id: string
  role_name: string
  scope?: string
  assigned_at: string
}

export interface CreateCustomRoleRequest {
  role_name: string
  description?: string
  permissions?: string[]
}

/**
 * Response from POST /auth/roles (CreateCustomRoleHandler).
 * Note: uses role_name (not name) and includes created/created_by fields.
 */
export interface CreatedRoleResponse {
  role_name: string
  description: string
  permissions_count: number
  created: boolean
  created_by: string
}

export interface AssignRoleRequest {
  principal_id: string
  role_name: string
  scope?: string
}

// ---------------------------------------------------------------------------
// Phase 28: RBAC Management + Tool Access Policy types
// ---------------------------------------------------------------------------

/**
 * Role with full details from GET /auth/roles/all.
 * Includes permissions array and is_builtin flag, unlike the
 * lighter Role type from GET /auth/roles.
 */
export interface AllRole {
  name: string
  description: string
  permissions: string[]
  permissions_count: number
  is_builtin: boolean
}

export interface AllRolesResponse {
  roles: AllRole[]
  total: number
  builtin_count: number
  custom_count: number
}

/** Principal from GET /auth/principals. */
export interface Principal {
  principal_id: string
  roles: string[]
}

export interface PrincipalsResponse {
  principals: Principal[]
  total: number
}

/** Permission resource type from GET /auth/permissions. */
export interface PermissionResource {
  resource_type: string
  actions: string[]
}

/** Check-permission request body for POST /auth/check-permission. */
export interface CheckPermissionRequest {
  principal_id: string
  action: string
  resource_type: string
  resource_id?: string
}

/** Check-permission response from POST /auth/check-permission. */
export interface CheckPermissionResponse {
  principal_id: string
  action: string
  resource_type: string
  resource_id: string
  allowed: boolean
  granted_by_role: string | null
}

/** Tool access policy shape shared across get/set responses. */
export interface ToolAccessPolicy {
  scope: string
  target_id: string
  allow_list: string[]
  deny_list: string[]
}

/** Response from GET /auth/policies/:scope/:targetId. */
export interface ToolAccessPolicyResponse extends ToolAccessPolicy {
  found: boolean
}

/** Request body for POST /auth/policies/:scope/:targetId. */
export interface SetToolAccessPolicyRequest {
  allow_list: string[]
  deny_list: string[]
}

/** Request body for PATCH /auth/roles/:roleName. */
export interface UpdateRoleRequest {
  permissions: string[]
  description?: string
  updated_by?: string
}

/** Response from PATCH /auth/roles/:roleName. */
export interface UpdateRoleResponse {
  role_name: string
  description: string | null
  permissions_count: number
  updated: boolean
  updated_by: string
}

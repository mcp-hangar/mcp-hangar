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

export interface Role {
  role_name: string
  name?: string
  description?: string
  permissions: string[]
  builtin: boolean
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

export interface AssignRoleRequest {
  principal_id: string
  role_name: string
  scope?: string
}

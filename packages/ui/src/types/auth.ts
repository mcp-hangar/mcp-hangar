export interface ApiKey {
  key_id: string
  principal_id: string
  name?: string
  created_at: string
  expires_at?: string
  last_used_at?: string
  scopes?: string[]
}

export interface CreateApiKeyRequest {
  principal_id: string
  name?: string
  expires_in_days?: number
  scopes?: string[]
}

export interface Role {
  role_id: string
  name: string
  description?: string
  permissions: string[]
  builtin: boolean
}

export interface RoleAssignment {
  principal_id: string
  role_id: string
  scope?: string
  assigned_at: string
}

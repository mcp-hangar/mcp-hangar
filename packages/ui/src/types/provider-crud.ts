import type { ProviderMode, ProviderState } from './provider'

export interface ProviderCreateRequest {
  provider_id: string
  mode: ProviderMode
  // subprocess mode
  command?: string[]
  // docker mode
  image?: string
  args?: string[]
  // remote mode
  endpoint?: string
  // behavior
  idle_ttl_s?: number | null
  description?: string
  // access
  allowed_tools?: string[]
  denied_tools?: string[]
}

export interface ProviderUpdateRequest {
  command?: string[]
  image?: string
  args?: string[]
  endpoint?: string
  idle_ttl_s?: number | null
  description?: string
  allowed_tools?: string[]
  denied_tools?: string[]
}

export interface ProviderCreateResponse {
  provider_id: string
  state: ProviderState
  mode: ProviderMode
}

export type GroupStrategy = 'round_robin' | 'weighted_round_robin' | 'least_connections' | 'random' | 'priority'

export interface GroupCreateRequest {
  group_id: string
  strategy: GroupStrategy
  description?: string
  min_healthy?: number
}

export interface GroupUpdateRequest {
  strategy?: GroupStrategy
  description?: string
  min_healthy?: number
}

export interface GroupCreateResponse {
  group_id: string
  strategy: GroupStrategy
  state: string
}

export interface GroupMemberAddRequest {
  provider_id: string
  weight?: number
  priority?: number
}

export interface GroupMemberUpdateRequest {
  weight?: number
  priority?: number
}

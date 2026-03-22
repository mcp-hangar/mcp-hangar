import type { CircuitBreakerInfo } from './provider'

/**
 * Group member as returned by GET /groups/{id} in to_status_dict().
 */
export interface GroupMember {
  id: string
  state: string
  in_rotation: boolean
  weight: number
  priority: number
  consecutive_failures: number
}

/**
 * Group summary as returned by GET /groups/ and GET /groups/{id}.
 * Matches ProviderGroup.to_status_dict() in provider_group.py.
 */
export interface GroupSummary {
  group_id: string
  description?: string
  state?: string
  strategy: string
  min_healthy?: number
  healthy_count: number
  total_members: number
  is_available?: boolean
  circuit_open: boolean
  circuit_breaker?: CircuitBreakerInfo | null
}

export interface GroupDetails extends GroupSummary {
  members: GroupMember[]
}

/**
 * Discovery source status as returned by GET /discovery/sources.
 * Matches SourceStatus.to_dict() in discovery_service.py.
 */
export interface DiscoverySourceStatus {
  source_id: string
  source_type: string
  mode: string
  is_healthy: boolean
  is_enabled: boolean
  last_discovery: string | null
  providers_count: number
  error_message: string | null
}

/**
 * Discovery source spec as managed by DiscoveryRegistry.
 * Matches DiscoverySourceSpec.to_dict() in value_objects/discovery.py.
 */
export interface DiscoverySourceSpec {
  source_id: string
  source_type: 'docker' | 'filesystem' | 'kubernetes' | 'entrypoint'
  mode: 'additive' | 'authoritative'
  enabled: boolean
  config: Record<string, unknown>
}

export interface RegisterSourceRequest {
  source_type: 'docker' | 'filesystem' | 'kubernetes' | 'entrypoint'
  mode: 'additive' | 'authoritative'
  enabled?: boolean
  config?: Record<string, unknown>
}

export interface UpdateSourceRequest {
  mode?: 'additive' | 'authoritative'
  enabled?: boolean
  config?: Record<string, unknown>
}

/**
 * Pending provider as returned by GET /discovery/pending.
 * Matches DiscoveredProvider.to_dict() in discovered_provider.py.
 */
export interface PendingProvider {
  name: string
  source_type: string
  mode: string
  connection_info: Record<string, unknown>
  metadata: Record<string, unknown>
  fingerprint: string
  discovered_at: string
  last_seen_at: string
  ttl_seconds: number
  is_expired: boolean
}

/**
 * Quarantined provider record as returned by GET /discovery/quarantined.
 * The backend returns a dict keyed by provider name:
 * { name: { provider: PendingProvider, reason: string, quarantine_time: string } }
 * The API client normalises this to an array of QuarantinedProvider.
 */
export interface QuarantinedProvider {
  name: string
  provider: PendingProvider
  reason: string
  quarantine_time: string
}

export interface SystemInfo {
  version: string
  uptime_seconds: number
  mode: string
  providers_total: number
  providers_ready: number
  started_at?: string
}

export interface SystemMetrics {
  total_providers: number
  providers_by_state: Record<string, number>
  total_tool_calls: number
  error_rate?: number
}

export interface GroupMember {
  provider_id: string
  state: string
  health?: string
}

export interface GroupSummary {
  group_id: string
  strategy: string
  member_count: number
  healthy_count: number
  circuit_breaker?: { state: string }
}

export interface GroupDetails extends GroupSummary {
  members: GroupMember[]
}

export interface DiscoverySourceStatus {
  source_id: string
  source_type: string
  healthy: boolean
  last_scan?: string
  provider_count: number
  error?: string
}

export interface PendingProvider {
  provider_id: string
  source_id: string
  discovered_at: string
  command?: string[]
  mode?: string
}

export interface QuarantinedProvider extends PendingProvider {
  reason: string
  quarantined_at: string
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

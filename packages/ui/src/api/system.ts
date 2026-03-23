import type { SystemInfo, SystemMetrics } from '../types/system'
import { apiClient } from './client'

interface SystemApiResponse {
  system: {
    total_providers: number
    providers_by_state: Record<string, number>
    total_tools: number
    total_invocations: number
    total_failures: number
    overall_success_rate: number
    uptime_seconds: number
    version: string
  }
}

export const systemApi = {
  info: (): Promise<SystemInfo> =>
    apiClient.get<SystemApiResponse>('/system').then((res) => {
      const s = res.system
      const readyCount = s.providers_by_state?.['ready'] ?? 0
      return {
        version: s.version,
        uptime_seconds: s.uptime_seconds,
        mode: 'http',
        providers_total: s.total_providers,
        providers_ready: readyCount,
      }
    }),
  metrics: (): Promise<SystemMetrics> =>
    apiClient.get<SystemApiResponse>('/system').then((res) => {
      const s = res.system
      const errorRate =
        s.total_invocations > 0 ? s.total_failures / s.total_invocations : undefined
      return {
        total_providers: s.total_providers,
        providers_by_state: s.providers_by_state,
        total_tool_calls: s.total_invocations,
        error_rate: errorRate,
      }
    }),
}

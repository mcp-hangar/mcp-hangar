import { apiClient } from './client'
import type {
  ConfigExportResponse,
  ConfigBackupRequest,
  ConfigBackupResponse,
  ConfigDiffResponse,
} from '../types/config'

export const configApi = {
  current: () => apiClient.get<{ config: Record<string, unknown> }>('/config'),
  // Backend returns { status: string, result: unknown } on reload
  reload: () => apiClient.post<{ status: string; result: unknown; message?: string }>('/config/reload'),
  export: () => apiClient.post<ConfigExportResponse>('/config/export'),
  backup: (req?: ConfigBackupRequest) => apiClient.post<ConfigBackupResponse>('/config/backup', req),
  diff: () => apiClient.get<ConfigDiffResponse>('/config/diff'),
}

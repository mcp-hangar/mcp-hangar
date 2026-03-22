export interface ConfigExportResponse {
  yaml: string
}

export interface ConfigBackupRequest {
  config_path?: string
}

export interface ConfigBackupResponse {
  path: string
}

export interface ConfigDiffResponse {
  has_diff: boolean
  diff: string
  on_disk: Record<string, unknown>
  in_memory: Record<string, unknown>
}

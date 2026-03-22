/**
 * MCP Catalog provider entry as returned by the catalog API.
 * Matches McpProviderEntry.to_dict() in domain/model/catalog.py.
 */
export interface McpProviderEntry {
  entry_id: string
  name: string
  description: string
  mode: 'subprocess' | 'docker' | 'remote'
  command: string[]
  image: string | null
  tags: string[]
  verified: boolean
  source: 'builtin' | 'custom'
  required_env: string[]
  builtin: boolean
}

export interface AddCatalogEntryRequest {
  name: string
  description: string
  mode: 'subprocess' | 'docker' | 'remote'
  command?: string[]
  image?: string
  tags?: string[]
  required_env?: string[]
}

export interface CatalogListResponse {
  entries: McpProviderEntry[]
  total: number
}

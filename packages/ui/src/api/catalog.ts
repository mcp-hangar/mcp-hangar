import type { AddCatalogEntryRequest, CatalogListResponse, McpProviderEntry } from '../types/catalog'
import { apiClient } from './client'

export const catalogApi = {
  list: (params?: { search?: string; tags?: string }) => {
    const searchParams = new URLSearchParams()
    if (params?.search) searchParams.set('search', params.search)
    if (params?.tags) searchParams.set('tags', params.tags)
    const qs = searchParams.toString()
    return apiClient.get<CatalogListResponse>(`/catalog${qs ? `?${qs}` : ''}`)
  },

  get: (entryId: string) => apiClient.get<McpProviderEntry>(`/catalog/${entryId}`),

  add: (body: AddCatalogEntryRequest) => apiClient.post<{ entry_id: string; added: boolean }>('/catalog/entries', body),

  remove: (entryId: string) => apiClient.delete<{ entry_id: string; deleted: boolean }>(`/catalog/entries/${entryId}`),

  deploy: (entryId: string) => apiClient.post<{ provider_id: string; deployed: boolean }>(`/catalog/${entryId}/deploy`),
}

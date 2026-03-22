import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Search } from 'lucide-react'

import { EmptyState } from '@/components/ui'
import { catalogApi } from '@/api/catalog'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import type { McpProviderEntry } from '@/types/catalog'

import { CatalogEntryCard } from './CatalogEntryCard'
import { CatalogEntryDrawer } from './CatalogEntryDrawer'
import { DeployDialog } from './DeployDialog'
import { AddEntryDrawer } from './AddEntryDrawer'

export function CatalogPage(): JSX.Element {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
  const [selectedEntry, setSelectedEntry] = useState<McpProviderEntry | null>(null)
  const [isAddOpen, setIsAddOpen] = useState(false)
  const [deployEntry, setDeployEntry] = useState<McpProviderEntry | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(timer)
  }, [search])

  const { data } = useQuery({
    queryKey: queryKeys.catalog.list({ search: debouncedSearch, tags: selectedTag ?? undefined }),
    queryFn: () => catalogApi.list({ search: debouncedSearch || undefined, tags: selectedTag ?? undefined }),
  })

  const entries = data?.entries ?? []
  const total = data?.total ?? 0

  const allTags = useMemo(() => {
    const tagSet = new Set<string>()
    for (const entry of entries) {
      for (const tag of entry.tags) {
        tagSet.add(tag)
      }
    }
    return Array.from(tagSet).sort()
  }, [entries])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900">Catalog</h1>
        <button
          type="button"
          onClick={() => setIsAddOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          <Plus size={16} />
          Add Entry
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search catalog entries..."
          className="w-full rounded-md border border-gray-300 pl-9 pr-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      {/* Tag filter */}
      {allTags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setSelectedTag(null)}
            className={cn(
              'rounded-full px-3 py-1 text-xs font-medium transition-colors',
              selectedTag === null ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            )}
          >
            All
          </button>
          {allTags.map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => setSelectedTag(tag === selectedTag ? null : tag)}
              className={cn(
                'rounded-full px-3 py-1 text-xs font-medium transition-colors',
                tag === selectedTag ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* Grid */}
      {entries.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {entries.map((entry) => (
            <CatalogEntryCard key={entry.entry_id} entry={entry} onClick={() => setSelectedEntry(entry)} />
          ))}
        </div>
      ) : (
        <EmptyState message="No catalog entries found." />
      )}

      {/* Footer */}
      <p className="text-xs text-gray-400">
        Showing {entries.length} of {total} entries
      </p>

      {/* Drawers and dialogs */}
      <CatalogEntryDrawer
        entry={selectedEntry}
        open={selectedEntry !== null}
        onClose={() => setSelectedEntry(null)}
        onDeploy={(entry) => {
          setSelectedEntry(null)
          setDeployEntry(entry)
        }}
      />
      <DeployDialog entry={deployEntry} open={deployEntry !== null} onClose={() => setDeployEntry(null)} />
      <AddEntryDrawer open={isAddOpen} onClose={() => setIsAddOpen(false)} />
    </div>
  )
}

import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Plus, Search } from 'lucide-react'

import { EmptyState, PageContainer } from '@/components/ui'
import { catalogApi } from '@/api/catalog'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { staggerContainer, staggerItem } from '@/lib/animations'
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
    <PageContainer className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-text-primary">Catalog</h1>
        <button
          type="button"
          onClick={() => setIsAddOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover transition-colors"
        >
          <Plus size={16} />
          Add Entry
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-faint" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search catalog entries..."
          className="w-full rounded-lg border border-border-strong bg-surface pl-9 pr-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
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
              selectedTag === null
                ? 'bg-accent text-white'
                : 'bg-surface-tertiary text-text-muted hover:bg-surface-secondary'
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
                tag === selectedTag
                  ? 'bg-accent text-white'
                  : 'bg-surface-tertiary text-text-muted hover:bg-surface-secondary'
              )}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* Grid */}
      {entries.length > 0 ? (
        <motion.div
          variants={staggerContainer}
          initial="hidden"
          animate="visible"
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
        >
          {entries.map((entry) => (
            <motion.div key={entry.entry_id} variants={staggerItem}>
              <CatalogEntryCard entry={entry} onClick={() => setSelectedEntry(entry)} />
            </motion.div>
          ))}
        </motion.div>
      ) : (
        <EmptyState message="No catalog entries found." />
      )}

      {/* Footer */}
      <p className="text-xs text-text-faint">
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
    </PageContainer>
  )
}

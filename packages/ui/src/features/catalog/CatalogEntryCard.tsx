import { CheckCircle } from 'lucide-react'

import { cn } from '@/lib/cn'
import type { McpProviderEntry } from '@/types/catalog'

interface CatalogEntryCardProps {
  entry: McpProviderEntry
  onClick: () => void
}

const MODE_BADGE_STYLES: Record<McpProviderEntry['mode'], string> = {
  subprocess: 'bg-surface-tertiary text-text-secondary',
  docker: 'bg-accent-surface text-accent-text',
  remote: 'bg-warning-surface text-warning-text',
}

export function CatalogEntryCard({ entry, onClick }: CatalogEntryCardProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className="bg-surface rounded-xl border border-border p-4 hover:shadow-md transition-all duration-150 cursor-pointer text-left w-full hover:-translate-y-0.5"
    >
      <div className="flex items-center gap-2 mb-2">
        <span className={cn('inline-block rounded px-2 py-0.5 text-xs font-medium', MODE_BADGE_STYLES[entry.mode])}>
          {entry.mode}
        </span>
        {entry.verified && <CheckCircle size={16} className="text-success" />}
      </div>

      <p className="font-medium text-text-primary">{entry.name}</p>

      <p className="text-sm text-text-muted line-clamp-2 mt-1">{entry.description}</p>

      {entry.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-3">
          {entry.tags.map((tag) => (
            <span
              key={tag}
              className="inline-block rounded-full bg-surface-tertiary px-2 py-0.5 text-xs text-text-muted"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 mt-3 text-xs">
        {entry.required_env.length > 0 && (
          <span className="text-warning-text">{entry.required_env.length} env vars required</span>
        )}
        <span className="text-text-faint ml-auto">{entry.source}</span>
      </div>
    </button>
  )
}

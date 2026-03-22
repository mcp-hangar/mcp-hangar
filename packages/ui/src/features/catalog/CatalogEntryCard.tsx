import { CheckCircle } from 'lucide-react'

import { cn } from '@/lib/cn'
import type { McpProviderEntry } from '@/types/catalog'

interface CatalogEntryCardProps {
  entry: McpProviderEntry
  onClick: () => void
}

const MODE_BADGE_STYLES: Record<McpProviderEntry['mode'], string> = {
  subprocess: 'bg-gray-100 text-gray-700',
  docker: 'bg-blue-100 text-blue-700',
  remote: 'bg-purple-100 text-purple-700',
}

export function CatalogEntryCard({ entry, onClick }: CatalogEntryCardProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className="bg-white rounded-lg border border-gray-200 p-4 hover:shadow-md transition cursor-pointer text-left w-full"
    >
      <div className="flex items-center gap-2 mb-2">
        <span className={cn('inline-block rounded px-2 py-0.5 text-xs font-medium', MODE_BADGE_STYLES[entry.mode])}>
          {entry.mode}
        </span>
        {entry.verified && <CheckCircle size={16} className="text-green-500" />}
      </div>

      <p className="font-medium text-gray-900">{entry.name}</p>

      <p className="text-sm text-gray-500 line-clamp-2 mt-1">{entry.description}</p>

      {entry.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-3">
          {entry.tags.map((tag) => (
            <span key={tag} className="inline-block rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
              {tag}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 mt-3 text-xs">
        {entry.required_env.length > 0 && (
          <span className="text-amber-600">{entry.required_env.length} env vars required</span>
        )}
        <span className="text-gray-400 ml-auto">{entry.source}</span>
      </div>
    </button>
  )
}

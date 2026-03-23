import { useQuery } from '@tanstack/react-query'
import { configApi } from '../../api/config'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'
import { ActionButton, LoadingSpinner } from '../../components/ui'
import { RefreshCw, CheckCircle } from 'lucide-react'

function classForDiffLine(line: string): string {
  if (line.startsWith('---') || line.startsWith('+++')) {
    return 'font-bold text-text-primary'
  }
  if (line.startsWith('@@')) {
    return 'text-accent bg-accent-surface'
  }
  if (line.startsWith('+')) {
    return 'text-success-text bg-success-surface'
  }
  if (line.startsWith('-')) {
    return 'text-danger-text bg-danger-surface'
  }
  return 'text-text-muted'
}

export function DiffTab(): JSX.Element {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: queryKeys.config.diff(),
    queryFn: configApi.diff,
  })

  if (isLoading) {
    return <LoadingSpinner />
  }

  if (error) {
    return <p className="text-sm text-danger">Failed to load configuration diff.</p>
  }

  const diffLines = data?.diff ? data.diff.split('\n').filter((l) => l.length > 0) : []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-text-muted">
          Compare the on-disk config file with the current in-memory configuration.
        </p>
        <ActionButton variant="ghost" onClick={() => refetch()} isLoading={isFetching}>
          <RefreshCw className="h-4 w-4" />
          Refresh
        </ActionButton>
      </div>

      {data && !data.has_diff && (
        <div className="flex items-center gap-2 rounded-xl border border-success-surface bg-success-surface p-4">
          <CheckCircle className="h-5 w-5 text-success" />
          <p className="text-sm font-medium text-success-text">
            Configuration is in sync. No differences between on-disk and in-memory state.
          </p>
        </div>
      )}

      {data && data.has_diff && diffLines.length > 0 && (
        <div className="overflow-auto rounded-xl border bg-surface shadow-xs">
          <pre className="p-4 text-xs font-mono leading-relaxed">
            {diffLines.map((line, i) => (
              <div key={i} className={cn('px-2 -mx-2', classForDiffLine(line))}>
                {line}
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  )
}

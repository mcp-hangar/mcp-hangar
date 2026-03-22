import { useQuery } from '@tanstack/react-query'
import { configApi } from '../../api/config'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'
import { ActionButton, LoadingSpinner } from '../../components/ui'
import { RefreshCw, CheckCircle } from 'lucide-react'

function classForDiffLine(line: string): string {
  if (line.startsWith('---') || line.startsWith('+++')) {
    return 'font-bold text-gray-900'
  }
  if (line.startsWith('@@')) {
    return 'text-blue-600 bg-blue-50'
  }
  if (line.startsWith('+')) {
    return 'text-green-700 bg-green-50'
  }
  if (line.startsWith('-')) {
    return 'text-red-700 bg-red-50'
  }
  return 'text-gray-600'
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
    return <p className="text-sm text-red-600">Failed to load configuration diff.</p>
  }

  const diffLines = data?.diff ? data.diff.split('\n').filter((l) => l.length > 0) : []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          Compare the on-disk config file with the current in-memory configuration.
        </p>
        <ActionButton variant="ghost" onClick={() => refetch()} isLoading={isFetching}>
          <RefreshCw className="h-4 w-4" />
          Refresh
        </ActionButton>
      </div>

      {data && !data.has_diff && (
        <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 p-4">
          <CheckCircle className="h-5 w-5 text-green-600" />
          <p className="text-sm font-medium text-green-800">
            Configuration is in sync. No differences between on-disk and in-memory state.
          </p>
        </div>
      )}

      {data && data.has_diff && diffLines.length > 0 && (
        <div className="overflow-auto rounded-lg border bg-white">
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

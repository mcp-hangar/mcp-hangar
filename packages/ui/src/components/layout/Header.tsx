import { useQuery } from '@tanstack/react-query'
import { systemApi } from '../../api/system'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'

function SystemStatusBadge(): JSX.Element {
  const { data, isError, isLoading } = useQuery({
    queryKey: queryKeys.system.info(),
    queryFn: () => systemApi.info(),
    refetchInterval: 30_000,
    retry: 2,
  })

  const status = isLoading ? 'loading' : isError ? 'error' : 'ok'

  return (
    <div className="flex items-center gap-1.5 text-xs text-gray-500">
      <span
        className={cn('h-2 w-2 rounded-full', {
          'bg-gray-400 animate-pulse': status === 'loading',
          'bg-red-500': status === 'error',
          'bg-green-500': status === 'ok',
        })}
      />
      {status === 'ok' && data
        ? `v${data.version} · ${data.providers_ready}/${data.providers_total} ready`
        : status === 'error'
          ? 'Backend offline'
          : 'Connecting...'}
    </div>
  )
}

export function Header(): JSX.Element {
  return (
    <header className="h-14 shrink-0 border-b border-gray-200 bg-white flex items-center justify-between px-6">
      <h1 className="text-sm font-medium text-gray-700">Management Console</h1>
      <SystemStatusBadge />
    </header>
  )
}

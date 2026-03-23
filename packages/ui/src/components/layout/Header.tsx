import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { systemApi } from '../../api/system'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'
import { ThemeToggle } from './ThemeToggle'

function SystemStatusBadge(): JSX.Element {
  const { data, isError, isLoading } = useQuery({
    queryKey: queryKeys.system.info(),
    queryFn: () => systemApi.info(),
    refetchInterval: 30_000,
    retry: 2,
  })

  const status = isLoading ? 'loading' : isError ? 'error' : 'ok'

  return (
    <div className="flex items-center gap-2 text-xs text-text-muted">
      <span className="relative flex h-2 w-2">
        {status === 'ok' && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-40" />
        )}
        <span
          className={cn('relative inline-flex h-2 w-2 rounded-full', {
            'bg-text-faint animate-pulse': status === 'loading',
            'bg-danger': status === 'error',
            'bg-success': status === 'ok',
          })}
        />
      </span>
      <span className="font-medium">
        {status === 'ok' && data
          ? `v${data.version} · ${data.providers_ready}/${data.providers_total} ready`
          : status === 'error'
            ? 'Backend offline'
            : 'Connecting...'}
      </span>
    </div>
  )
}

export function Header(): JSX.Element {
  return (
    <motion.header
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: 0.05 }}
      className="h-12 shrink-0 border-b border-border bg-surface/80 backdrop-blur-sm flex items-center justify-between px-6"
    >
      <div />
      <div className="flex items-center gap-3">
        <SystemStatusBadge />
        <div className="w-px h-4 bg-border" />
        <ThemeToggle />
      </div>
    </motion.header>
  )
}

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { configApi } from '../../api/config'
import { ActionButton, LoadingSpinner } from '../../components/ui'
import { cn } from '../../lib/cn'
import { Download, Copy, Archive } from 'lucide-react'

export function ExportBackupTab(): JSX.Element {
  const [exportedYaml, setExportedYaml] = useState<string | null>(null)
  const [copySuccess, setCopySuccess] = useState(false)

  const exportMutation = useMutation({
    mutationFn: configApi.export,
    onSuccess: (result) => {
      setExportedYaml(result.yaml)
      toast.success('Configuration exported.')
    },
    onError: () => {
      toast.error('Export failed.')
      setExportedYaml(null)
    },
  })

  const backupMutation = useMutation({
    mutationFn: () => configApi.backup(),
    onSuccess: (result) => {
      toast.success(`Backup created at ${result.path}`)
    },
    onError: () => {
      toast.error('Backup failed.')
    },
  })

  function handleCopyToClipboard(): void {
    if (!exportedYaml) return
    navigator.clipboard.writeText(exportedYaml).then(() => {
      setCopySuccess(true)
      toast.success('Copied to clipboard.')
      setTimeout(() => setCopySuccess(false), 2000)
    })
  }

  function handleDownload(): void {
    if (!exportedYaml) return
    const blob = new Blob([exportedYaml], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'config.yaml'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">
      {/* Export YAML section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-text-primary">Export Configuration</h3>
            <p className="text-xs text-text-muted">Serialize the current in-memory configuration to YAML format.</p>
          </div>
          <ActionButton variant="primary" onClick={() => exportMutation.mutate()} isLoading={exportMutation.isPending}>
            Export YAML
          </ActionButton>
        </div>

        {exportMutation.isPending && <LoadingSpinner />}

        {exportedYaml && (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <button
                onClick={handleCopyToClipboard}
                className={cn(
                  'inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium',
                  copySuccess
                    ? 'bg-success-surface text-success-text'
                    : 'bg-surface-tertiary text-text-secondary hover:bg-surface-secondary'
                )}
              >
                <Copy className="h-3 w-3" />
                {copySuccess ? 'Copied' : 'Copy'}
              </button>
              <button
                onClick={handleDownload}
                className="inline-flex items-center gap-1 rounded bg-surface-tertiary px-2 py-1 text-xs font-medium text-text-secondary hover:bg-surface-secondary"
              >
                <Download className="h-3 w-3" />
                Download
              </button>
            </div>
            <pre className="max-h-96 overflow-auto rounded-xl border bg-surface-secondary p-4 text-xs font-mono text-text-secondary">
              {exportedYaml}
            </pre>
          </div>
        )}
      </div>

      {/* Backup section */}
      <div className="border-t pt-6">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-text-primary">Create Backup</h3>
            <p className="text-xs text-text-muted">
              Create a rotating backup of the config file on disk (up to 5 backups).
            </p>
          </div>
          <ActionButton variant="ghost" onClick={() => backupMutation.mutate()} isLoading={backupMutation.isPending}>
            <Archive className="h-4 w-4" />
            Create Backup
          </ActionButton>
        </div>
      </div>
    </div>
  )
}

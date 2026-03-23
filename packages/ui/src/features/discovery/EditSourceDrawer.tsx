import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { Drawer } from '@/components/ui/Drawer'
import { discoveryApi } from '@/api/discovery'
import { queryKeys } from '@/lib/queryKeys'
import type { DiscoverySourceStatus, UpdateSourceRequest } from '@/types/system'

type SourceMode = 'additive' | 'authoritative'

interface EditSourceForm {
  mode: SourceMode
  enabled: boolean
  paths: string
  labels_filter: string
  namespace: string
  label_selector: string
  package_patterns: string
}

interface EditSourceDrawerProps {
  source: DiscoverySourceStatus | null
  open: boolean
  onClose: () => void
}

function formFromSource(source: DiscoverySourceStatus): EditSourceForm {
  return {
    mode: (source.mode === 'authoritative' ? 'authoritative' : 'additive') as SourceMode,
    enabled: source.is_enabled,
    paths: '',
    labels_filter: '',
    namespace: '',
    label_selector: '',
    package_patterns: '',
  }
}

function buildConfig(sourceType: string, form: EditSourceForm): Record<string, unknown> | undefined {
  switch (sourceType) {
    case 'filesystem': {
      const paths = form.paths
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean)
      return paths.length > 0 ? { paths } : undefined
    }
    case 'docker':
      return form.labels_filter.trim() ? { labels_filter: form.labels_filter.trim() } : undefined
    case 'kubernetes': {
      const config: Record<string, unknown> = {}
      if (form.namespace.trim()) config.namespace = form.namespace.trim()
      if (form.label_selector.trim()) config.label_selector = form.label_selector.trim()
      return Object.keys(config).length > 0 ? config : undefined
    }
    case 'entrypoint': {
      const patterns = form.package_patterns
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean)
      return patterns.length > 0 ? { package_patterns: patterns } : undefined
    }
    default:
      return undefined
  }
}

export function EditSourceDrawer({ source, open, onClose }: EditSourceDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<EditSourceForm>(() =>
    source
      ? formFromSource(source)
      : formFromSource({
          source_id: '',
          source_type: 'filesystem',
          mode: 'additive',
          is_healthy: true,
          is_enabled: true,
          last_discovery: null,
          providers_count: 0,
          error_message: null,
        })
  )
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open && source) {
      setForm(formFromSource(source))
      setError(null)
    }
  }, [open, source])

  const updateMutation = useMutation({
    mutationFn: ({ sourceId, req }: { sourceId: string; req: UpdateSourceRequest }) =>
      discoveryApi.updateSource(sourceId, req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all })
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleSubmit = () => {
    if (!source) return
    const config = buildConfig(source.source_type, form)
    const req: UpdateSourceRequest = {
      mode: form.mode,
      enabled: form.enabled,
    }
    if (config) req.config = config
    updateMutation.mutate({ sourceId: source.source_id, req })
  }

  const setField = <K extends keyof EditSourceForm>(field: K, value: EditSourceForm[K]) => {
    setForm((f) => ({ ...f, [field]: value }))
    setError(null)
  }

  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen) onClose()
  }

  const sourceType = source?.source_type ?? 'filesystem'

  const footer = (
    <>
      <div />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onClose}
          className="px-4 py-1.5 text-sm border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={updateMutation.isPending}
          className="px-4 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </>
  )

  return (
    <Drawer open={open} onOpenChange={handleOpenChange} title="Edit Discovery Source" footer={footer}>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">Source ID</label>
          <p className="px-3 py-2 text-sm text-text-secondary bg-surface-secondary rounded-lg border border-border">
            {source?.source_id ?? '\u2014'}
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">Source Type</label>
          <p className="px-3 py-2 text-sm text-text-secondary bg-surface-secondary rounded-lg border border-border">
            {source?.source_type ?? '\u2014'}
          </p>
          <p className="text-xs text-text-muted mt-1">Source type cannot be changed.</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">Mode</label>
          <select
            value={form.mode}
            onChange={(e) => setField('mode', e.target.value as SourceMode)}
            className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="additive">Additive</option>
            <option value="authoritative">Authoritative</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="edit-source-enabled"
            checked={form.enabled}
            onChange={(e) => setField('enabled', e.target.checked)}
            className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
          />
          <label htmlFor="edit-source-enabled" className="text-sm text-text-secondary">
            Enabled
          </label>
        </div>

        {/* Config section based on source type */}
        <div className="border-t border-border pt-4">
          <h4 className="text-sm font-medium text-text-secondary mb-3">Configuration</h4>

          {sourceType === 'filesystem' && (
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">Paths (one per line)</label>
              <textarea
                value={form.paths}
                onChange={(e) => setField('paths', e.target.value)}
                placeholder={'/etc/mcp/providers\n/opt/providers'}
                rows={4}
                className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          )}

          {sourceType === 'docker' && (
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">Labels Filter</label>
              <input
                type="text"
                value={form.labels_filter}
                onChange={(e) => setField('labels_filter', e.target.value)}
                placeholder="mcp.provider=true"
                className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          )}

          {sourceType === 'kubernetes' && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">Namespace</label>
                <input
                  type="text"
                  value={form.namespace}
                  onChange={(e) => setField('namespace', e.target.value)}
                  placeholder="default"
                  className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">Label Selector</label>
                <input
                  type="text"
                  value={form.label_selector}
                  onChange={(e) => setField('label_selector', e.target.value)}
                  placeholder="app=mcp-provider"
                  className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </div>
            </div>
          )}

          {sourceType === 'entrypoint' && (
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Package Patterns (one per line)
              </label>
              <textarea
                value={form.package_patterns}
                onChange={(e) => setField('package_patterns', e.target.value)}
                placeholder={'mcp-*\nmcp_provider_*'}
                rows={4}
                className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          )}
        </div>

        {error && <p className="text-sm text-danger mt-2">{error}</p>}
      </div>
    </Drawer>
  )
}

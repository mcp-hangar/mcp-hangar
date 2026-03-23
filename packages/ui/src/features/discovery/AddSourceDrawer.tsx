import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { Drawer } from '@/components/ui/Drawer'
import { discoveryApi } from '@/api/discovery'
import { queryKeys } from '@/lib/queryKeys'
import type { RegisterSourceRequest } from '@/types/system'

type SourceType = RegisterSourceRequest['source_type']
type SourceMode = RegisterSourceRequest['mode']

interface AddSourceForm {
  source_type: SourceType
  mode: SourceMode
  enabled: boolean
  paths: string
  labels_filter: string
  namespace: string
  label_selector: string
  package_patterns: string
}

const INITIAL_FORM: AddSourceForm = {
  source_type: 'filesystem',
  mode: 'additive',
  enabled: true,
  paths: '',
  labels_filter: '',
  namespace: '',
  label_selector: '',
  package_patterns: '',
}

interface AddSourceDrawerProps {
  open: boolean
  onClose: () => void
}

function buildConfig(form: AddSourceForm): Record<string, unknown> {
  switch (form.source_type) {
    case 'filesystem': {
      const paths = form.paths
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean)
      return paths.length > 0 ? { paths } : {}
    }
    case 'docker':
      return form.labels_filter.trim() ? { labels_filter: form.labels_filter.trim() } : {}
    case 'kubernetes': {
      const config: Record<string, unknown> = {}
      if (form.namespace.trim()) config.namespace = form.namespace.trim()
      if (form.label_selector.trim()) config.label_selector = form.label_selector.trim()
      return config
    }
    case 'entrypoint': {
      const patterns = form.package_patterns
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean)
      return patterns.length > 0 ? { package_patterns: patterns } : {}
    }
    default:
      return {}
  }
}

export function AddSourceDrawer({ open, onClose }: AddSourceDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<AddSourceForm>(INITIAL_FORM)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setForm(INITIAL_FORM)
      setError(null)
    }
  }, [open])

  const createMutation = useMutation({
    mutationFn: (req: RegisterSourceRequest) => discoveryApi.registerSource(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.discovery.all })
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleSubmit = () => {
    const config = buildConfig(form)
    const req: RegisterSourceRequest = {
      source_type: form.source_type,
      mode: form.mode,
      enabled: form.enabled,
      config: Object.keys(config).length > 0 ? config : undefined,
    }
    createMutation.mutate(req)
  }

  const setField = <K extends keyof AddSourceForm>(field: K, value: AddSourceForm[K]) => {
    setForm((f) => ({ ...f, [field]: value }))
    setError(null)
  }

  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen) onClose()
  }

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
          disabled={createMutation.isPending}
          className="px-4 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {createMutation.isPending ? 'Creating...' : 'Create Source'}
        </button>
      </div>
    </>
  )

  return (
    <Drawer open={open} onOpenChange={handleOpenChange} title="Add Discovery Source" footer={footer}>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">
            Source Type <span className="text-danger">*</span>
          </label>
          <select
            value={form.source_type}
            onChange={(e) => setField('source_type', e.target.value as SourceType)}
            className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="filesystem">Filesystem</option>
            <option value="docker">Docker</option>
            <option value="kubernetes">Kubernetes</option>
            <option value="entrypoint">Entrypoint</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">
            Mode <span className="text-danger">*</span>
          </label>
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
            id="add-source-enabled"
            checked={form.enabled}
            onChange={(e) => setField('enabled', e.target.checked)}
            className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
          />
          <label htmlFor="add-source-enabled" className="text-sm text-text-secondary">
            Enabled
          </label>
        </div>

        {/* Config section based on source type */}
        <div className="border-t border-border pt-4">
          <h4 className="text-sm font-medium text-text-secondary mb-3">Configuration</h4>

          {form.source_type === 'filesystem' && (
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

          {form.source_type === 'docker' && (
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

          {form.source_type === 'kubernetes' && (
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

          {form.source_type === 'entrypoint' && (
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

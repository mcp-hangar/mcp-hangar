import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Terminal, Box, Globe } from 'lucide-react'
import { Drawer } from '@/components/ui/Drawer'
import { providersApi } from '@/api/providers'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import type { ProviderDetails, ProviderMode } from '@/types/provider'
import type { ProviderUpdateRequest } from '@/types/provider-crud'

const STEPS = ['Mode', 'Connection', 'Behavior', 'Access'] as const
type Step = 0 | 1 | 2 | 3

interface ProviderFormData {
  mode: ProviderMode
  provider_id: string
  command: string
  image: string
  args: string
  endpoint: string
  idle_ttl_s: string
  description: string
  allowed_tools: string
  denied_tools: string
}

function buildInitialForm(provider: ProviderDetails): ProviderFormData {
  return {
    mode: provider.mode,
    provider_id: provider.provider_id,
    command: provider.command ? provider.command.join(',') : '',
    image: (provider.meta?.image as string | undefined) ?? '',
    args: Array.isArray(provider.meta?.args) ? (provider.meta.args as string[]).join(',') : '',
    endpoint: (provider.meta?.endpoint as string | undefined) ?? '',
    idle_ttl_s: provider.idle_ttl_s != null ? String(provider.idle_ttl_s) : '',
    description: provider.description ?? '',
    allowed_tools: '',
    denied_tools: '',
  }
}

function buildUpdateRequest(form: ProviderFormData): ProviderUpdateRequest {
  const splitLines = (s: string) =>
    s
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
  const splitCommas = (s: string) =>
    s
      .split(',')
      .map((l) => l.trim())
      .filter(Boolean)

  const req: ProviderUpdateRequest = {}

  if (form.mode === 'subprocess' && form.command.trim()) {
    req.command = splitCommas(form.command)
  } else if (form.mode === 'docker') {
    if (form.image.trim()) req.image = form.image.trim()
    if (form.args.trim()) req.args = splitCommas(form.args)
  } else if (form.mode === 'remote') {
    if (form.endpoint.trim()) req.endpoint = form.endpoint.trim()
  }

  if (form.idle_ttl_s.trim()) {
    const n = parseInt(form.idle_ttl_s, 10)
    if (!isNaN(n)) req.idle_ttl_s = n
  }
  if (form.description.trim()) req.description = form.description.trim()
  if (form.allowed_tools.trim()) req.allowed_tools = splitLines(form.allowed_tools)
  if (form.denied_tools.trim()) req.denied_tools = splitLines(form.denied_tools)

  return req
}

interface ProviderEditDrawerProps {
  provider: ProviderDetails
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ProviderEditDrawer({ provider, open, onOpenChange }: ProviderEditDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>(0)
  const [form, setForm] = useState<ProviderFormData>(() => buildInitialForm(provider))
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setForm(buildInitialForm(provider))
      setStep(0)
      setError(null)
    }
  }, [open, provider])

  const updateMutation = useMutation({
    mutationFn: (req: ProviderUpdateRequest) => providersApi.update(provider.provider_id, req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all })
      onOpenChange(false)
    },
    onError: (e: Error) => setError(e.message),
  })

  const setField = (field: keyof ProviderFormData, value: string) => {
    setForm((f) => ({ ...f, [field]: value }))
    setError(null)
  }

  const footer = (
    <>
      <div>
        {step > 0 && (
          <button
            type="button"
            onClick={() => setStep((s) => (s - 1) as Step)}
            className="px-4 py-2 text-sm font-medium text-text-secondary bg-surface border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
          >
            Back
          </button>
        )}
      </div>
      <div className="flex gap-2">
        {step < 3 ? (
          <button
            type="button"
            onClick={() => setStep((s) => (s + 1) as Step)}
            className="px-4 py-2 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover"
          >
            Next
          </button>
        ) : (
          <button
            type="button"
            onClick={() => updateMutation.mutate(buildUpdateRequest(form))}
            disabled={updateMutation.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
          </button>
        )}
      </div>
    </>
  )

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Edit Provider" width="lg" footer={footer}>
      {/* Stepper */}
      <nav className="flex items-center mb-6">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center">
            <div className="flex flex-col items-center gap-1">
              <span
                className={cn(
                  'w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium',
                  i < step
                    ? 'bg-accent text-white'
                    : i === step
                      ? 'bg-accent text-white ring-2 ring-accent'
                      : 'bg-surface-tertiary text-text-faint'
                )}
              >
                {i < step ? '✓' : i + 1}
              </span>
              <span
                className={cn('text-xs whitespace-nowrap', i === step ? 'text-accent font-medium' : 'text-text-faint')}
              >
                {label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={cn('h-px w-10 mx-2 mb-5', i < step ? 'bg-accent' : 'bg-border')} />
            )}
          </div>
        ))}
      </nav>

      {/* Step 0: Mode (read-only) */}
      {step === 0 && (
        <div className="space-y-3">
          <p className="text-sm text-text-muted mb-4">Provider mode cannot be changed after creation.</p>
          <div className="grid grid-cols-1 gap-3">
            {(
              [
                { mode: 'subprocess', Icon: Terminal, name: 'Local Process', desc: 'Run a command on this machine' },
                { mode: 'docker', Icon: Box, name: 'Docker Container', desc: 'Run in an isolated container' },
                { mode: 'remote', Icon: Globe, name: 'Remote Endpoint', desc: 'Connect to an HTTP/SSE endpoint' },
              ] as const
            ).map(({ mode, Icon, name, desc }) => (
              <div
                key={mode}
                className={cn(
                  'flex items-start gap-3 rounded-lg border-2 p-4 cursor-default',
                  form.mode === mode ? 'border-accent bg-accent-surface' : 'border-border opacity-50'
                )}
              >
                <Icon size={20} className={form.mode === mode ? 'text-accent' : 'text-text-muted'} />
                <div>
                  <p
                    className={cn('text-sm font-medium', form.mode === mode ? 'text-accent-text' : 'text-text-primary')}
                  >
                    {name}
                  </p>
                  <p className="text-xs text-text-muted mt-0.5">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Step 1: Connection */}
      {step === 1 && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Provider ID</label>
            <p className="px-3 py-2 text-sm text-text-secondary bg-surface-secondary rounded-lg border border-border">
              {form.provider_id}
            </p>
            <p className="text-xs text-text-muted mt-1">Provider ID cannot be changed.</p>
          </div>

          {form.mode === 'subprocess' && (
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">Command</label>
              <input
                type="text"
                value={form.command}
                onChange={(e) => setField('command', e.target.value)}
                placeholder="python,-m,my_server"
                className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
              <p className="text-xs text-text-muted mt-1">Enter values separated by commas.</p>
            </div>
          )}

          {form.mode === 'docker' && (
            <>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">Image</label>
                <input
                  type="text"
                  value={form.image}
                  onChange={(e) => setField('image', e.target.value)}
                  placeholder="my-org/my-provider:latest"
                  className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">Args</label>
                <input
                  type="text"
                  value={form.args}
                  onChange={(e) => setField('args', e.target.value)}
                  placeholder="--flag,value"
                  className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
                <p className="text-xs text-text-muted mt-1">Enter values separated by commas.</p>
              </div>
            </>
          )}

          {form.mode === 'remote' && (
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">Endpoint URL</label>
              <input
                type="url"
                value={form.endpoint}
                onChange={(e) => setField('endpoint', e.target.value)}
                placeholder="https://my-provider.example.com/mcp"
                className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          )}
        </div>
      )}

      {/* Step 2: Behavior */}
      {step === 2 && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Idle TTL (seconds)</label>
            <input
              type="number"
              value={form.idle_ttl_s}
              onChange={(e) => setField('idle_ttl_s', e.target.value)}
              placeholder="300 — provider stops after this many idle seconds"
              className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Description</label>
            <input
              type="text"
              value={form.description}
              onChange={(e) => setField('description', e.target.value)}
              placeholder="Brief description of this provider"
              className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
        </div>
      )}

      {/* Step 3: Access */}
      {step === 3 && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Allowed Tools</label>
            <textarea
              value={form.allowed_tools}
              onChange={(e) => setField('allowed_tools', e.target.value)}
              rows={4}
              placeholder="Enter tool name patterns, one per line. Leave empty for no restriction."
              className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent resize-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Denied Tools</label>
            <textarea
              value={form.denied_tools}
              onChange={(e) => setField('denied_tools', e.target.value)}
              rows={4}
              placeholder="Enter tool name patterns, one per line. Leave empty for no restriction."
              className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent resize-none"
            />
          </div>
        </div>
      )}

      {error && <p className="text-sm text-danger mt-4">{error}</p>}
    </Drawer>
  )
}

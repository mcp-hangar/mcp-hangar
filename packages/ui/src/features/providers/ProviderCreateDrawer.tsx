import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Terminal, Box, Globe } from 'lucide-react'
import { Drawer } from '@/components/ui/Drawer'
import { providersApi } from '@/api/providers'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import type { ProviderMode } from '@/types/provider'
import type { ProviderCreateRequest } from '@/types/provider-crud'

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

const defaultForm: ProviderFormData = {
  mode: 'subprocess',
  provider_id: '',
  command: '',
  image: '',
  args: '',
  endpoint: '',
  idle_ttl_s: '',
  description: '',
  allowed_tools: '',
  denied_tools: '',
}

function isStepValid(step: Step, form: ProviderFormData): boolean {
  if (step === 0) return true
  if (step === 1) {
    if (!form.provider_id.trim() || !/^[a-z0-9-]+$/.test(form.provider_id.trim())) return false
    if (form.mode === 'subprocess') return form.command.trim().length > 0
    if (form.mode === 'docker') return form.image.trim().length > 0
    if (form.mode === 'remote') {
      const ep = form.endpoint.trim()
      return ep.startsWith('http://') || ep.startsWith('https://')
    }
    return false
  }
  return true
}

function buildRequest(form: ProviderFormData): ProviderCreateRequest {
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

  const req: ProviderCreateRequest = {
    provider_id: form.provider_id.trim(),
    mode: form.mode,
  }

  if (form.mode === 'subprocess') {
    req.command = splitCommas(form.command)
  } else if (form.mode === 'docker') {
    req.image = form.image.trim()
    if (form.args.trim()) req.args = splitCommas(form.args)
  } else if (form.mode === 'remote') {
    req.endpoint = form.endpoint.trim()
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

interface ProviderCreateDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ProviderCreateDrawer({ open, onOpenChange }: ProviderCreateDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>(0)
  const [form, setForm] = useState<ProviderFormData>(defaultForm)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setStep(0)
      setForm(defaultForm)
      setError(null)
    }
  }, [open])

  const createMutation = useMutation({
    mutationFn: (req: ProviderCreateRequest) => providersApi.create(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all })
      onOpenChange(false)
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleNext = () => {
    if (step < 3) setStep((s) => (s + 1) as Step)
  }

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
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Back
          </button>
        )}
      </div>
      <div className="flex gap-2">
        {step < 3 ? (
          <button
            type="button"
            onClick={handleNext}
            disabled={!isStepValid(step, form)}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
        ) : (
          <button
            type="button"
            onClick={() => createMutation.mutate(buildRequest(form))}
            disabled={createMutation.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {createMutation.isPending ? 'Creating...' : 'Create Provider'}
          </button>
        )}
      </div>
    </>
  )

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Create Provider" width="lg" footer={footer}>
      {/* Stepper */}
      <nav className="flex items-center mb-6">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center">
            <div className="flex flex-col items-center gap-1">
              <span
                className={cn(
                  'w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium',
                  i < step
                    ? 'bg-blue-600 text-white'
                    : i === step
                      ? 'bg-blue-600 text-white ring-2 ring-blue-200'
                      : 'bg-gray-100 text-gray-400'
                )}
              >
                {i < step ? '✓' : i + 1}
              </span>
              <span
                className={cn('text-xs whitespace-nowrap', i === step ? 'text-blue-600 font-medium' : 'text-gray-400')}
              >
                {label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={cn('h-px w-10 mx-2 mb-5', i < step ? 'bg-blue-600' : 'bg-gray-200')} />
            )}
          </div>
        ))}
      </nav>

      {/* Step content */}
      {step === 0 && (
        <div className="space-y-3">
          <p className="text-sm text-gray-600 mb-4">Select how this provider will run.</p>
          <div className="grid grid-cols-1 gap-3">
            {(
              [
                { mode: 'subprocess', Icon: Terminal, name: 'Local Process', desc: 'Run a command on this machine' },
                { mode: 'docker', Icon: Box, name: 'Docker Container', desc: 'Run in an isolated container' },
                { mode: 'remote', Icon: Globe, name: 'Remote Endpoint', desc: 'Connect to an HTTP/SSE endpoint' },
              ] as const
            ).map(({ mode, Icon, name, desc }) => (
              <button
                key={mode}
                type="button"
                onClick={() => setField('mode', mode)}
                className={cn(
                  'flex items-start gap-3 rounded-lg border-2 p-4 text-left transition-colors',
                  form.mode === mode ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-300'
                )}
              >
                <Icon size={20} className={form.mode === mode ? 'text-blue-600' : 'text-gray-500'} />
                <div>
                  <p className={cn('text-sm font-medium', form.mode === mode ? 'text-blue-900' : 'text-gray-900')}>
                    {name}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">{desc}</p>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Provider ID <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={form.provider_id}
              onChange={(e) => setField('provider_id', e.target.value)}
              placeholder="e.g. my-provider"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500 mt-1">Lowercase letters, numbers and hyphens only.</p>
          </div>

          {form.mode === 'subprocess' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Command <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={form.command}
                onChange={(e) => setField('command', e.target.value)}
                placeholder="python,-m,my_server"
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-500 mt-1">Enter values separated by commas.</p>
            </div>
          )}

          {form.mode === 'docker' && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Image <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={form.image}
                  onChange={(e) => setField('image', e.target.value)}
                  placeholder="my-org/my-provider:latest"
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Args</label>
                <input
                  type="text"
                  value={form.args}
                  onChange={(e) => setField('args', e.target.value)}
                  placeholder="--flag,value"
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
                <p className="text-xs text-gray-500 mt-1">Enter values separated by commas.</p>
              </div>
            </>
          )}

          {form.mode === 'remote' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Endpoint URL <span className="text-red-500">*</span>
              </label>
              <input
                type="url"
                value={form.endpoint}
                onChange={(e) => setField('endpoint', e.target.value)}
                placeholder="https://my-provider.example.com/mcp"
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Idle TTL (seconds)</label>
            <input
              type="number"
              value={form.idle_ttl_s}
              onChange={(e) => setField('idle_ttl_s', e.target.value)}
              placeholder="300 — provider stops after this many idle seconds"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <input
              type="text"
              value={form.description}
              onChange={(e) => setField('description', e.target.value)}
              placeholder="Brief description of this provider"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Allowed Tools</label>
            <textarea
              value={form.allowed_tools}
              onChange={(e) => setField('allowed_tools', e.target.value)}
              rows={4}
              placeholder="Enter tool name patterns, one per line. Leave empty for no restriction."
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Denied Tools</label>
            <textarea
              value={form.denied_tools}
              onChange={(e) => setField('denied_tools', e.target.value)}
              rows={4}
              placeholder="Enter tool name patterns, one per line. Leave empty for no restriction."
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
            />
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-600 mt-4">{error}</p>}
    </Drawer>
  )
}

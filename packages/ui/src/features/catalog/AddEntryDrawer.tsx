import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { Drawer } from '@/components/ui/Drawer'
import { catalogApi } from '@/api/catalog'
import { queryKeys } from '@/lib/queryKeys'
import type { AddCatalogEntryRequest, McpProviderEntry } from '@/types/catalog'

interface AddEntryDrawerProps {
  open: boolean
  onClose: () => void
}

interface EntryForm {
  name: string
  description: string
  mode: McpProviderEntry['mode']
  command: string
  image: string
  tags: string[]
  required_env: string[]
}

const INITIAL_FORM: EntryForm = {
  name: '',
  description: '',
  mode: 'subprocess',
  command: '',
  image: '',
  tags: [],
  required_env: [],
}

export function AddEntryDrawer({ open, onClose }: AddEntryDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<EntryForm>(INITIAL_FORM)
  const [tagInput, setTagInput] = useState('')
  const [envInput, setEnvInput] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setForm(INITIAL_FORM)
      setTagInput('')
      setEnvInput('')
      setError(null)
    }
  }, [open])

  const isValid = form.name.trim().length > 0 && form.description.trim().length > 0 && form.mode.length > 0

  const addMutation = useMutation({
    mutationFn: (req: AddCatalogEntryRequest) => catalogApi.add(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.catalog.all })
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleSubmit = () => {
    if (!isValid) return
    const req: AddCatalogEntryRequest = {
      name: form.name.trim(),
      description: form.description.trim(),
      mode: form.mode,
    }
    const cmd = form.command.trim()
    if (cmd) req.command = cmd.split(/\s+/)
    if (form.image.trim()) req.image = form.image.trim()
    if (form.tags.length > 0) req.tags = form.tags
    if (form.required_env.length > 0) req.required_env = form.required_env
    addMutation.mutate(req)
  }

  const setField = <K extends keyof EntryForm>(field: K, value: EntryForm[K]) => {
    setForm((f) => ({ ...f, [field]: value }))
    setError(null)
  }

  const addTag = (raw: string) => {
    const value = raw.trim()
    if (value && !form.tags.includes(value)) {
      setField('tags', [...form.tags, value])
    }
    setTagInput('')
  }

  const removeTag = (tag: string) => {
    setField(
      'tags',
      form.tags.filter((t) => t !== tag)
    )
  }

  const addEnv = (raw: string) => {
    const value = raw.trim()
    if (value && !form.required_env.includes(value)) {
      setField('required_env', [...form.required_env, value])
    }
    setEnvInput('')
  }

  const removeEnv = (env: string) => {
    setField(
      'required_env',
      form.required_env.filter((e) => e !== env)
    )
  }

  const handleTagKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag(tagInput)
    }
    if (e.key === 'Backspace' && tagInput === '' && form.tags.length > 0) {
      removeTag(form.tags[form.tags.length - 1])
    }
  }

  const handleEnvKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addEnv(envInput)
    }
    if (e.key === 'Backspace' && envInput === '' && form.required_env.length > 0) {
      removeEnv(form.required_env[form.required_env.length - 1])
    }
  }

  const showCommand = form.mode === 'subprocess' || form.mode === 'docker'
  const showImage = form.mode === 'docker'

  const footer = (
    <>
      <div />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onClose}
          className="px-4 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!isValid || addMutation.isPending}
          className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {addMutation.isPending ? 'Adding...' : 'Add Entry'}
        </button>
      </div>
    </>
  )

  return (
    <Drawer
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose()
      }}
      title="Add Catalog Entry"
      footer={footer}
    >
      <div className="space-y-4">
        {/* Name */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Name <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={form.name}
            onChange={(e) => setField('name', e.target.value)}
            placeholder="e.g. my-tool-provider"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {/* Description */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Description <span className="text-red-500">*</span>
          </label>
          <textarea
            value={form.description}
            onChange={(e) => setField('description', e.target.value)}
            placeholder="What does this provider do?"
            rows={3}
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
          />
        </div>

        {/* Mode */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Mode <span className="text-red-500">*</span>
          </label>
          <select
            value={form.mode}
            onChange={(e) => setField('mode', e.target.value as McpProviderEntry['mode'])}
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="subprocess">subprocess</option>
            <option value="docker">docker</option>
            <option value="remote">remote</option>
          </select>
        </div>

        {/* Command */}
        {showCommand && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Command</label>
            <input
              type="text"
              value={form.command}
              onChange={(e) => setField('command', e.target.value)}
              placeholder="e.g. python -m my_server"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-400 mt-1">Space-separated command and arguments</p>
          </div>
        )}

        {/* Image */}
        {showImage && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Image</label>
            <input
              type="text"
              value={form.image}
              onChange={(e) => setField('image', e.target.value)}
              placeholder="e.g. ghcr.io/org/provider:latest"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
        )}

        {/* Tags */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Tags</label>
          <div className="flex flex-wrap items-center gap-1 rounded-md border border-gray-300 px-2 py-1.5 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500">
            {form.tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center gap-0.5 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
              >
                {tag}
                <button
                  type="button"
                  onClick={() => removeTag(tag)}
                  className="ml-0.5 text-gray-400 hover:text-gray-600"
                  aria-label={`Remove tag ${tag}`}
                >
                  x
                </button>
              </span>
            ))}
            <input
              type="text"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={handleTagKeyDown}
              onBlur={() => {
                if (tagInput.trim()) addTag(tagInput)
              }}
              placeholder={form.tags.length === 0 ? 'Type and press Enter' : ''}
              className="flex-1 min-w-[80px] border-0 bg-transparent text-sm focus:outline-none p-0.5"
            />
          </div>
        </div>

        {/* Required env vars */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Required Env Vars</label>
          <div className="flex flex-wrap items-center gap-1 rounded-md border border-gray-300 px-2 py-1.5 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500">
            {form.required_env.map((env) => (
              <span
                key={env}
                className="inline-flex items-center gap-0.5 rounded bg-amber-50 border border-amber-200 px-2 py-0.5 text-xs font-mono text-amber-700"
              >
                {env}
                <button
                  type="button"
                  onClick={() => removeEnv(env)}
                  className="ml-0.5 text-amber-400 hover:text-amber-600"
                  aria-label={`Remove env var ${env}`}
                >
                  x
                </button>
              </span>
            ))}
            <input
              type="text"
              value={envInput}
              onChange={(e) => setEnvInput(e.target.value)}
              onKeyDown={handleEnvKeyDown}
              onBlur={() => {
                if (envInput.trim()) addEnv(envInput)
              }}
              placeholder={form.required_env.length === 0 ? 'Type and press Enter' : ''}
              className="flex-1 min-w-[80px] border-0 bg-transparent text-sm font-mono focus:outline-none p-0.5"
            />
          </div>
        </div>

        {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
      </div>
    </Drawer>
  )
}

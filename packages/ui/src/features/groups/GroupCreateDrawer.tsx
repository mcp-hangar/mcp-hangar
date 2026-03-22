import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Drawer } from '@/components/ui/Drawer'
import { groupsApi } from '@/api/groups'
import { queryKeys } from '@/lib/queryKeys'
import type { GroupCreateRequest, GroupStrategy } from '@/types/provider-crud'

interface GroupForm {
  group_id: string
  strategy: GroupStrategy
  description: string
  min_healthy: string
}

const INITIAL_FORM: GroupForm = {
  group_id: '',
  strategy: 'round_robin',
  description: '',
  min_healthy: '',
}

interface GroupCreateDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function GroupCreateDrawer({ open, onOpenChange }: GroupCreateDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<GroupForm>(INITIAL_FORM)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setForm(INITIAL_FORM)
      setError(null)
    }
  }, [open])

  const isValid = form.group_id.trim().length > 0 && form.strategy.length > 0

  const createMutation = useMutation({
    mutationFn: (req: GroupCreateRequest) => groupsApi.create(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
      onOpenChange(false)
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleSubmit = () => {
    if (!isValid) return
    const req: GroupCreateRequest = {
      group_id: form.group_id.trim(),
      strategy: form.strategy,
    }
    if (form.description.trim()) req.description = form.description.trim()
    if (form.min_healthy.trim()) {
      const n = parseInt(form.min_healthy, 10)
      if (!isNaN(n)) req.min_healthy = n
    }
    createMutation.mutate(req)
  }

  const setField = (field: keyof GroupForm, value: string) => {
    setForm((f) => ({ ...f, [field]: value }))
    setError(null)
  }

  const footer = (
    <>
      <div />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          className="px-4 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!isValid || createMutation.isPending}
          className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {createMutation.isPending ? 'Creating...' : 'Create Group'}
        </button>
      </div>
    </>
  )

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Create Group" footer={footer}>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Group ID <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={form.group_id}
            onChange={(e) => setField('group_id', e.target.value)}
            placeholder="e.g. primary-tools"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Strategy <span className="text-red-500">*</span>
          </label>
          <select
            value={form.strategy}
            onChange={(e) => setField('strategy', e.target.value as GroupStrategy)}
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="round_robin">Round Robin</option>
            <option value="weighted">Weighted</option>
            <option value="priority">Priority</option>
            <option value="failover">Failover</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
          <input
            type="text"
            value={form.description}
            onChange={(e) => setField('description', e.target.value)}
            placeholder="Brief description of this group"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Min Healthy</label>
          <input
            type="number"
            value={form.min_healthy}
            onChange={(e) => setField('min_healthy', e.target.value)}
            placeholder="1"
            min="0"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
      </div>
    </Drawer>
  )
}

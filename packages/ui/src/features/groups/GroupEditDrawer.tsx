import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Drawer } from '@/components/ui/Drawer'
import { groupsApi } from '@/api/groups'
import { queryKeys } from '@/lib/queryKeys'
import type { GroupSummary } from '@/types/system'
import type { GroupUpdateRequest, GroupStrategy } from '@/types/provider-crud'

interface GroupForm {
  strategy: GroupStrategy
  description: string
  min_healthy: string
}

interface GroupEditDrawerProps {
  group: GroupSummary
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function GroupEditDrawer({ group, open, onOpenChange }: GroupEditDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<GroupForm>({
    strategy: group.strategy as GroupStrategy,
    description: group.description ?? '',
    min_healthy: group.min_healthy != null ? String(group.min_healthy) : '',
  })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setForm({
        strategy: group.strategy as GroupStrategy,
        description: group.description ?? '',
        min_healthy: group.min_healthy != null ? String(group.min_healthy) : '',
      })
      setError(null)
    }
  }, [open, group])

  const updateMutation = useMutation({
    mutationFn: (req: GroupUpdateRequest) => groupsApi.update(group.group_id, req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
      onOpenChange(false)
    },
    onError: (e: Error) => setError(e.message),
  })

  const handleSubmit = () => {
    const req: GroupUpdateRequest = { strategy: form.strategy }
    if (form.description.trim()) req.description = form.description.trim()
    if (form.min_healthy.trim()) {
      const n = parseInt(form.min_healthy, 10)
      if (!isNaN(n)) req.min_healthy = n
    }
    updateMutation.mutate(req)
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
    <Drawer open={open} onOpenChange={onOpenChange} title="Edit Group" footer={footer}>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">Group ID</label>
          <p className="px-3 py-2 text-sm text-text-secondary bg-surface-secondary rounded-lg border border-border">
            {group.group_id}
          </p>
          <p className="text-xs text-text-muted mt-1">Group ID cannot be changed.</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">Strategy</label>
          <select
            value={form.strategy}
            onChange={(e) => setField('strategy', e.target.value as GroupStrategy)}
            className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="round_robin">Round Robin</option>
            <option value="weighted_round_robin">Weighted Round Robin</option>
            <option value="least_connections">Least Connections</option>
            <option value="random">Random</option>
            <option value="priority">Priority</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">Description</label>
          <input
            type="text"
            value={form.description}
            onChange={(e) => setField('description', e.target.value)}
            placeholder="Brief description of this group"
            className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1">Min Healthy</label>
          <input
            type="number"
            value={form.min_healthy}
            onChange={(e) => setField('min_healthy', e.target.value)}
            placeholder="1"
            min="0"
            className="w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </div>

        {error && <p className="text-sm text-danger mt-2">{error}</p>}
      </div>
    </Drawer>
  )
}

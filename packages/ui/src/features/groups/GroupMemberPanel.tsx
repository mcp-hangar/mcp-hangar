import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil } from 'lucide-react'
import { Drawer } from '@/components/ui/Drawer'
import { EmptyState } from '@/components/ui/EmptyState'
import { groupsApi } from '@/api/groups'
import { providersApi } from '@/api/providers'
import { queryKeys } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import type { GroupMember } from '@/types/system'
import type { GroupMemberAddRequest, GroupMemberUpdateRequest } from '@/types/provider-crud'

interface GroupMemberPanelProps {
  groupId: string
  members: GroupMember[]
}

export function GroupMemberPanel({ groupId, members }: GroupMemberPanelProps): JSX.Element {
  const queryClient = useQueryClient()
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editForm, setEditForm] = useState<{ weight: string; priority: string }>({
    weight: '',
    priority: '',
  })
  const [addOpen, setAddOpen] = useState(false)
  const editWeightRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId) editWeightRef.current?.focus()
  }, [editingId])

  const removeMutation = useMutation({
    mutationFn: (memberId: string) => groupsApi.removeMember(groupId, memberId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
      setConfirmingId(null)
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ memberId, req }: { memberId: string; req: GroupMemberUpdateRequest }) =>
      groupsApi.updateMember(groupId, memberId, req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
      setEditingId(null)
    },
  })

  const handleEditStart = (member: GroupMember) => {
    setEditingId(member.id)
    setEditForm({ weight: String(member.weight), priority: String(member.priority) })
    setConfirmingId(null)
  }

  const handleEditSave = (memberId: string) => {
    const req: GroupMemberUpdateRequest = {}
    const w = parseInt(editForm.weight, 10)
    const p = parseInt(editForm.priority, 10)
    if (!isNaN(w)) req.weight = w
    if (!isNaN(p)) req.priority = p
    updateMutation.mutate({ memberId, req })
  }

  return (
    <>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Members</span>
            <span className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">{members.length}</span>
          </div>
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="flex items-center gap-1 px-2 py-1 text-xs text-blue-600 border border-blue-200 rounded hover:bg-blue-50"
          >
            <Plus size={12} />
            Add
          </button>
        </div>

        {members.length === 0 ? (
          <EmptyState message="No members in this group." className="py-4" />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left text-xs font-medium text-gray-500 py-2 pr-3">Provider</th>
                <th className="text-left text-xs font-medium text-gray-500 py-2 pr-3">State</th>
                <th className="text-left text-xs font-medium text-gray-500 py-2 pr-3">Weight</th>
                <th className="text-left text-xs font-medium text-gray-500 py-2 pr-3">Priority</th>
                <th className="text-left text-xs font-medium text-gray-500 py-2">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {members.map((member) => {
                const isEditing = editingId === member.id
                const isConfirming = confirmingId === member.id

                return (
                  <tr key={member.id} className="hover:bg-gray-50">
                    <td className="py-2 pr-3">
                      <Link to={`/providers/${member.id}`} className="text-blue-600 hover:underline text-sm">
                        {member.id}
                      </Link>
                    </td>
                    <td className="py-2 pr-3 text-sm text-gray-700">{member.state}</td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          ref={editWeightRef}
                          type="number"
                          value={editForm.weight}
                          onChange={(e) => setEditForm((f) => ({ ...f, weight: e.target.value }))}
                          className="w-16 rounded border border-gray-300 px-1.5 py-0.5 text-sm focus:border-blue-500 focus:outline-none"
                        />
                      ) : (
                        <span className="text-sm text-gray-700">{member.weight}</span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          type="number"
                          value={editForm.priority}
                          onChange={(e) => setEditForm((f) => ({ ...f, priority: e.target.value }))}
                          className="w-16 rounded border border-gray-300 px-1.5 py-0.5 text-sm focus:border-blue-500 focus:outline-none"
                        />
                      ) : (
                        <span className="text-sm text-gray-700">{member.priority}</span>
                      )}
                    </td>
                    <td className="py-2">
                      {isEditing ? (
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => handleEditSave(member.id)}
                            disabled={updateMutation.isPending}
                            className="text-xs px-2 py-0.5 text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={() => setEditingId(null)}
                            className="text-xs px-2 py-0.5 text-gray-600 border border-gray-300 rounded hover:bg-gray-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : isConfirming ? (
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => removeMutation.mutate(member.id)}
                            disabled={removeMutation.isPending}
                            className="text-xs px-2 py-0.5 text-white bg-red-600 rounded hover:bg-red-700 disabled:opacity-50"
                          >
                            Remove
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmingId(null)}
                            className="text-xs px-2 py-0.5 text-gray-600 border border-gray-300 rounded hover:bg-gray-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => handleEditStart(member)}
                            className="p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
                            title="Edit weight and priority"
                          >
                            <Pencil size={12} />
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setConfirmingId(member.id)
                              setEditingId(null)
                            }}
                            className={cn('text-xs text-red-600 hover:underline')}
                          >
                            Remove
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <AddMemberDrawer groupId={groupId} open={addOpen} onOpenChange={setAddOpen} />
    </>
  )
}

interface AddMemberDrawerProps {
  groupId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

function AddMemberDrawer({ groupId, open, onOpenChange }: AddMemberDrawerProps): JSX.Element {
  const queryClient = useQueryClient()
  const [providerId, setProviderId] = useState('')
  const [weight, setWeight] = useState('1')
  const [priority, setPriority] = useState('0')
  const [query, setQuery] = useState('')
  const [addError, setAddError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setProviderId('')
      setWeight('1')
      setPriority('0')
      setQuery('')
      setAddError(null)
    }
  }, [open])

  const { data: providersData } = useQuery({
    queryKey: queryKeys.providers.list(),
    queryFn: () => providersApi.list(),
    enabled: open,
  })

  const suggestions = (providersData?.providers ?? [])
    .filter((p) => p.provider_id.toLowerCase().includes(query.toLowerCase()) && query.length > 0)
    .slice(0, 8)

  const addMutation = useMutation({
    mutationFn: (req: GroupMemberAddRequest) => groupsApi.addMember(groupId, req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
      onOpenChange(false)
    },
    onError: (e: Error) => setAddError(e.message),
  })

  const handleSubmit = () => {
    if (!providerId.trim()) return
    const req: GroupMemberAddRequest = { provider_id: providerId.trim() }
    const w = parseInt(weight, 10)
    const p = parseInt(priority, 10)
    if (!isNaN(w)) req.weight = w
    if (!isNaN(p)) req.priority = p
    addMutation.mutate(req)
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
          disabled={!providerId.trim() || addMutation.isPending}
          className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {addMutation.isPending ? 'Adding...' : 'Add Member'}
        </button>
      </div>
    </>
  )

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Add Member" footer={footer}>
      <div className="space-y-4">
        <div className="relative">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Provider ID <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={query || providerId}
            onChange={(e) => {
              const v = e.target.value
              setQuery(v)
              setProviderId(v)
              setAddError(null)
            }}
            placeholder="Search providers..."
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {suggestions.length > 0 && (
            <ul className="absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-md shadow-lg max-h-48 overflow-y-auto">
              {suggestions.map((p) => (
                <li
                  key={p.provider_id}
                  onClick={() => {
                    setProviderId(p.provider_id)
                    setQuery('')
                  }}
                  className="px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 cursor-pointer"
                >
                  {p.provider_id}
                  <span className="ml-2 text-xs text-gray-400">{p.state}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Weight</label>
          <input
            type="number"
            value={weight}
            onChange={(e) => setWeight(e.target.value)}
            min="1"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Priority</label>
          <input
            type="number"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            min="0"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {addError && <p className="text-sm text-red-600">{addError}</p>}
      </div>
    </Drawer>
  )
}

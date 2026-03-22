import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as Dialog from '@radix-ui/react-dialog'
import { Plus, Pencil, Trash2 } from 'lucide-react'
import { groupsApi } from '../../api/groups'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'
import { CircuitBreakerBadge, ActionButton, EmptyState, LoadingSpinner } from '../../components/ui'
import { GroupCreateDrawer } from './GroupCreateDrawer'
import { GroupEditDrawer } from './GroupEditDrawer'
import { GroupMemberPanel } from './GroupMemberPanel'
import type { GroupSummary } from '../../types/system'

interface GroupDeleteDialogProps {
  groupId: string
  open: boolean
  onOpenChange: (o: boolean) => void
  onConfirm: () => void
  isPending: boolean
}

function GroupDeleteDialog({ groupId, open, onOpenChange, onConfirm, isPending }: GroupDeleteDialogProps): JSX.Element {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 z-40" />
        <Dialog.Content className="fixed inset-0 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
            <Dialog.Title className="text-base font-semibold text-gray-900">Delete Group</Dialog.Title>
            <p className="text-sm text-gray-600">
              Delete <span className="font-medium">{groupId}</span>? This cannot be undone.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Dialog.Close asChild>
                <button
                  type="button"
                  className="px-4 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
                >
                  Cancel
                </button>
              </Dialog.Close>
              <button
                type="button"
                disabled={isPending}
                onClick={onConfirm}
                className="px-4 py-1.5 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isPending ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export function GroupsPage(): JSX.Element {
  const queryClient = useQueryClient()
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [editGroup, setEditGroup] = useState<GroupSummary | null>(null)
  const [deleteGroupId, setDeleteGroupId] = useState<string | null>(null)

  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: queryKeys.groups.list(),
    queryFn: () => groupsApi.list(),
    refetchInterval: 30_000,
  })

  const { data: detail } = useQuery({
    queryKey: queryKeys.groups.detail(selectedGroupId!),
    queryFn: () => groupsApi.get(selectedGroupId!),
    enabled: !!selectedGroupId,
  })

  const rebalanceMutation = useMutation({
    mutationFn: (id: string) => groupsApi.rebalance(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.groups.all }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => groupsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
      if (selectedGroupId === deleteGroupId) setSelectedGroupId(null)
      setDeleteGroupId(null)
    },
  })

  const groups = listData?.groups ?? []

  return (
    <div className="space-y-4 p-6">
      <h2 className="text-lg font-semibold text-gray-900">Groups</h2>
      <div className="flex gap-6">
        {/* Left panel — group list */}
        <div className="w-80 shrink-0">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Groups</h3>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="flex items-center gap-1 px-2.5 py-1 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700"
            >
              <Plus size={14} />
              New
            </button>
          </div>
          {listLoading ? (
            <LoadingSpinner />
          ) : groups.length === 0 ? (
            <EmptyState message="No groups configured." />
          ) : (
            <div className="space-y-2">
              {groups.map((group) => (
                <div
                  key={group.group_id}
                  onClick={() => setSelectedGroupId(group.group_id)}
                  className={cn(
                    'cursor-pointer rounded-lg border p-3 hover:bg-gray-50 transition-colors',
                    selectedGroupId === group.group_id ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate">{group.group_id}</span>
                    <div className="flex items-center gap-1 shrink-0">
                      {group.circuit_breaker && <CircuitBreakerBadge state={group.circuit_breaker.state} />}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setEditGroup(group)
                        }}
                        className="p-1 text-gray-400 hover:text-gray-600 rounded"
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteGroupId(group.group_id)
                        }}
                        className="p-1 text-gray-400 hover:text-red-500 rounded"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">{group.strategy}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {group.healthy_count}/{group.total_members} healthy
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right panel — group detail */}
        <div className="flex-1">
          {!selectedGroupId ? (
            <EmptyState message="Select a group to view details." />
          ) : !detail ? (
            <LoadingSpinner />
          ) : (
            <div className="bg-white rounded-lg border p-4 space-y-4">
              {/* Header */}
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <h3 className="text-lg font-semibold text-gray-900">{detail.group_id}</h3>
                  {detail.circuit_breaker && <CircuitBreakerBadge state={detail.circuit_breaker.state} />}
                </div>
                <ActionButton
                  variant="primary"
                  onClick={() => rebalanceMutation.mutate(selectedGroupId)}
                  isLoading={rebalanceMutation.isPending}
                >
                  Rebalance
                </ActionButton>
              </div>

              {/* Strategy */}
              <div>
                <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Strategy</span>
                <p className="text-sm text-gray-900 mt-0.5">{detail.strategy}</p>
              </div>

              {/* Members panel */}
              <GroupMemberPanel groupId={detail.group_id} members={detail.members} />
            </div>
          )}
        </div>
      </div>

      <GroupCreateDrawer open={createOpen} onOpenChange={setCreateOpen} />

      {editGroup && (
        <GroupEditDrawer
          key={editGroup.group_id}
          group={editGroup}
          open={!!editGroup}
          onOpenChange={(o) => {
            if (!o) setEditGroup(null)
          }}
        />
      )}

      {deleteGroupId && (
        <GroupDeleteDialog
          groupId={deleteGroupId}
          open={!!deleteGroupId}
          onOpenChange={(o) => {
            if (!o) setDeleteGroupId(null)
          }}
          onConfirm={() => deleteMutation.mutate(deleteGroupId)}
          isPending={deleteMutation.isPending}
        />
      )}
    </div>
  )
}

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import * as Dialog from '@radix-ui/react-dialog'
import { Plus, Pencil, Trash2, ChevronDown } from 'lucide-react'
import { groupsApi } from '../../api/groups'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'
import { modalVariants, overlayVariants, expandVariants } from '../../lib/animations'
import { CircuitBreakerBadge, ActionButton, EmptyState, LoadingSpinner, PageContainer } from '../../components/ui'
import { ToolAccessPolicyEditor } from '../../components/ui/ToolAccessPolicyEditor'
import { GroupCreateDrawer } from './GroupCreateDrawer'
import { GroupEditDrawer } from './GroupEditDrawer'
import { GroupMemberPanel } from './GroupMemberPanel'
import { useToolAccessPolicy } from '../../hooks/useToolAccessPolicy'
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
        <Dialog.Overlay asChild>
          <motion.div
            variants={overlayVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="fixed inset-0 bg-overlay z-40"
          />
        </Dialog.Overlay>
        <Dialog.Content className="fixed inset-0 flex items-center justify-center z-50 p-4">
          <motion.div
            variants={modalVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="bg-surface rounded-xl shadow-xl w-full max-w-md p-6 space-y-4"
          >
            <Dialog.Title className="text-base font-semibold text-text-primary">Delete Group</Dialog.Title>
            <p className="text-sm text-text-muted">
              Delete <span className="font-medium">{groupId}</span>? This cannot be undone.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Dialog.Close asChild>
                <button
                  type="button"
                  className="px-4 py-1.5 text-sm border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
                >
                  Cancel
                </button>
              </Dialog.Close>
              <button
                type="button"
                disabled={isPending}
                onClick={onConfirm}
                className="px-4 py-1.5 text-sm bg-danger text-white rounded-lg hover:bg-danger-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isPending ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </motion.div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

interface GroupPolicySectionProps {
  groupId: string
}

function GroupPolicySection({ groupId }: GroupPolicySectionProps): JSX.Element {
  const [tapOpen, setTapOpen] = useState(false)
  const [allowedTools, setAllowedTools] = useState<string[]>([])
  const [deniedTools, setDeniedTools] = useState<string[]>([])
  const [tapDirty, setTapDirty] = useState(false)

  const { policy, savePolicy, clearPolicy, isSaving, isClearing } = useToolAccessPolicy('group', groupId)

  // Sync local state when policy data changes
  const policyKey = policy ? `${policy.allow_list.join(',')}_${policy.deny_list.join(',')}` : ''
  const [lastPolicyKey, setLastPolicyKey] = useState('')
  if (policyKey !== lastPolicyKey) {
    setLastPolicyKey(policyKey)
    if (policy) {
      setAllowedTools(policy.allow_list)
      setDeniedTools(policy.deny_list)
      setTapDirty(false)
    }
  }

  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-surface-secondary transition-colors duration-150"
        onClick={() => setTapOpen((v) => !v)}
        aria-expanded={tapOpen}
      >
        <h4 className="text-sm font-medium text-text-secondary">Tool Access Policy</h4>
        <ChevronDown
          size={16}
          className={`text-text-faint transition-transform duration-200 ${tapOpen ? 'rotate-180' : ''}`}
        />
      </button>
      <AnimatePresence initial={false}>
        {tapOpen && (
          <motion.div
            variants={expandVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            style={{ overflow: 'hidden' }}
          >
            <div className="px-4 pb-4 space-y-4">
              <ToolAccessPolicyEditor
                allowedTools={allowedTools}
                deniedTools={deniedTools}
                onAllowedChange={(tools) => {
                  setAllowedTools(tools)
                  setTapDirty(true)
                }}
                onDeniedChange={(tools) => {
                  setDeniedTools(tools)
                  setTapDirty(true)
                }}
                disabled={isSaving || isClearing}
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={!tapDirty || isSaving}
                  onClick={() => savePolicy({ allow_list: allowedTools, deny_list: deniedTools })}
                  className="px-3 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isSaving ? 'Saving...' : 'Save Policy'}
                </button>
                <button
                  type="button"
                  disabled={isClearing}
                  onClick={() => clearPolicy()}
                  className="px-3 py-1.5 text-sm border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isClearing ? 'Clearing...' : 'Clear Policy'}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
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
    <PageContainer className="space-y-4 p-6">
      <h2 className="text-lg font-semibold text-text-primary">Groups</h2>
      <div className="flex gap-6">
        {/* Left panel -- group list */}
        <div className="w-80 shrink-0">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">Groups</h3>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="flex items-center gap-1 px-2.5 py-1 text-xs bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors"
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
                    'cursor-pointer rounded-xl border p-3 hover:bg-surface-secondary transition-colors',
                    selectedGroupId === group.group_id ? 'border-accent bg-accent-surface' : 'border-border bg-surface'
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-text-primary truncate">{group.group_id}</span>
                    <div className="flex items-center gap-1 shrink-0">
                      {group.circuit_breaker && <CircuitBreakerBadge state={group.circuit_breaker.state} />}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setEditGroup(group)
                        }}
                        className="p-1 text-text-faint hover:text-text-secondary rounded transition-colors"
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteGroupId(group.group_id)
                        }}
                        className="p-1 text-text-faint hover:text-danger rounded transition-colors"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mt-0.5">{group.strategy}</p>
                  <p className="text-xs text-text-muted mt-1">
                    {group.healthy_count}/{group.total_members} healthy
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right panel -- group detail */}
        <div className="flex-1">
          {!selectedGroupId ? (
            <EmptyState message="Select a group to view details." />
          ) : !detail ? (
            <LoadingSpinner />
          ) : (
            <div className="bg-surface rounded-xl border border-border p-4 space-y-4 shadow-xs">
              {/* Header */}
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <h3 className="text-lg font-semibold text-text-primary">{detail.group_id}</h3>
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
                <span className="text-xs font-medium text-text-muted uppercase tracking-wider">Strategy</span>
                <p className="text-sm text-text-primary mt-0.5">{detail.strategy}</p>
              </div>

              {/* Members panel */}
              <GroupMemberPanel groupId={detail.group_id} members={detail.members} />

              {/* Tool Access Policy */}
              <GroupPolicySection groupId={detail.group_id} />
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
    </PageContainer>
  )
}

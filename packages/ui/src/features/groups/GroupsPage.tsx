import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { groupsApi } from '../../api/groups'
import { queryKeys } from '../../lib/queryKeys'
import { cn } from '../../lib/cn'
import { CircuitBreakerBadge, ActionButton, EmptyState, LoadingSpinner } from '../../components/ui'

export function GroupsPage(): JSX.Element {
  const queryClient = useQueryClient()
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null)

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

  const groups = listData?.groups ?? []

  return (
    <div className="space-y-4 p-6">
      <h2 className="text-lg font-semibold text-gray-900">Groups</h2>
      <div className="flex gap-6">
        {/* Left panel — group list */}
        <div className="w-80 shrink-0">
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
                    {group.circuit_breaker && <CircuitBreakerBadge state={group.circuit_breaker.state} />}
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

              {/* Members table */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Members</span>
                  <span className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">
                    {detail.members.length}
                  </span>
                </div>
                {detail.members.length === 0 ? (
                  <EmptyState message="No members in this group." className="py-6" />
                ) : (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-100">
                        <th className="text-left text-xs font-medium text-gray-500 py-2 pr-4">Provider ID</th>
                        <th className="text-left text-xs font-medium text-gray-500 py-2 pr-4">State</th>
                        <th className="text-left text-xs font-medium text-gray-500 py-2">Health</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {detail.members.map((member) => (
                        <tr key={member.id}>
                          <td className="py-2 pr-4">
                            <Link to={`/providers/${member.id}`} className="text-blue-600 hover:underline text-sm">
                              {member.id}
                            </Link>
                          </td>
                          <td className="py-2 pr-4 text-sm text-gray-700">{member.state}</td>
                          <td className="py-2 text-sm text-gray-700">
                            {member.consecutive_failures > 0 ? `${member.consecutive_failures} failures` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

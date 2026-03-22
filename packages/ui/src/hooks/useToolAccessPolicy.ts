import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authApi } from '../api/auth'
import { queryKeys } from '../lib/queryKeys'
import type { SetToolAccessPolicyRequest, ToolAccessPolicyResponse } from '../types/auth'

interface UseToolAccessPolicyResult {
  policy: ToolAccessPolicyResponse | undefined
  isLoading: boolean
  savePolicy: (req: SetToolAccessPolicyRequest) => void
  clearPolicy: () => void
  isSaving: boolean
  isClearing: boolean
}

/**
 * Manage a tool access policy for a given scope and target.
 *
 * @param scope - "provider", "group", or "member"
 * @param targetId - The provider, group, or member ID
 */
export function useToolAccessPolicy(scope: string, targetId: string): UseToolAccessPolicyResult {
  const queryClient = useQueryClient()
  const queryKey = queryKeys.auth.policy(scope, targetId)

  const { data: policy, isLoading } = useQuery<ToolAccessPolicyResponse>({
    queryKey,
    queryFn: () => authApi.getToolAccessPolicy(scope, targetId),
    enabled: !!scope && !!targetId,
  })

  const saveMutation = useMutation({
    mutationFn: (req: SetToolAccessPolicyRequest) => authApi.setToolAccessPolicy(scope, targetId, req),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey })
    },
  })

  const clearMutation = useMutation({
    mutationFn: () => authApi.clearToolAccessPolicy(scope, targetId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey })
    },
  })

  return {
    policy,
    isLoading,
    savePolicy: (req: SetToolAccessPolicyRequest) => saveMutation.mutate(req),
    clearPolicy: () => clearMutation.mutate(),
    isSaving: saveMutation.isPending,
    isClearing: clearMutation.isPending,
  }
}

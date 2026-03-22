import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authApi } from '../../../api/auth'
import { queryKeys } from '../../../lib/queryKeys'
import type {
  AssignRoleRequest,
  CheckPermissionRequest,
  CheckPermissionResponse,
  PermissionResource,
  PrincipalsResponse,
} from '../../../types/auth'

/** Fetch all principals with at least one role assignment. */
export function usePrincipals() {
  return useQuery<PrincipalsResponse>({
    queryKey: queryKeys.auth.principals(),
    queryFn: () => authApi.listPrincipals(),
  })
}

/** Fetch all available permission resource types and actions. */
export function usePermissions() {
  return useQuery<{ permissions: PermissionResource[] }>({
    queryKey: queryKeys.auth.permissions(),
    queryFn: () => authApi.listPermissions(),
  })
}

/** Check if a principal has a specific permission. */
export function useCheckPermission() {
  return useMutation<CheckPermissionResponse, Error, CheckPermissionRequest>({
    mutationFn: (req) => authApi.checkPermission(req),
  })
}

/** Assign a role to a principal. Invalidates principals on success. */
export function useAssignRole() {
  const queryClient = useQueryClient()
  return useMutation<{ status: string }, Error, AssignRoleRequest>({
    mutationFn: (req) => authApi.assignRole(req),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.principals() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.assignments() })
    },
  })
}

/** Revoke a role from a principal. Invalidates principals on success. */
export function useRevokeRole() {
  const queryClient = useQueryClient()
  return useMutation<{ status: string }, Error, { principalId: string; roleName: string; scope?: string }>({
    mutationFn: ({ principalId, roleName, scope }) => authApi.revokeRole(principalId, roleName, scope),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.principals() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.assignments() })
    },
  })
}

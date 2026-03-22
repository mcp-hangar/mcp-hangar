import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authApi } from '../../../api/auth'
import { queryKeys } from '../../../lib/queryKeys'
import type {
  AllRolesResponse,
  AllRole,
  CreateCustomRoleRequest,
  CreatedRoleResponse,
  UpdateRoleRequest,
  UpdateRoleResponse,
} from '../../../types/auth'

/** Fetch all roles (builtin + custom) with full details. */
export function useAllRoles(includeBuiltin?: boolean) {
  return useQuery<AllRolesResponse>({
    queryKey: queryKeys.auth.allRoles(includeBuiltin),
    queryFn: () => authApi.listAllRoles(includeBuiltin),
  })
}

/** Fetch a single role by name. */
export function useRole(roleName: string) {
  return useQuery<{ found: boolean; role: AllRole | null }>({
    queryKey: queryKeys.auth.role(roleName),
    queryFn: () => authApi.getRole(roleName),
    enabled: roleName.length > 0,
  })
}

/** Delete a custom role. Invalidates allRoles on success. */
export function useDeleteRole() {
  const queryClient = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (roleName: string) => authApi.deleteRole(roleName),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.allRoles() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.roles() })
    },
  })
}

/** Update a custom role's permissions/description. Invalidates allRoles on success. */
export function useUpdateRole() {
  const queryClient = useQueryClient()
  return useMutation<UpdateRoleResponse, Error, { roleName: string; req: UpdateRoleRequest }>({
    mutationFn: ({ roleName, req }) => authApi.updateRole(roleName, req),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.allRoles() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.role(variables.roleName) })
    },
  })
}

/** Create a custom role. Invalidates allRoles on success. */
export function useCreateRole() {
  const queryClient = useQueryClient()
  return useMutation<CreatedRoleResponse, Error, CreateCustomRoleRequest>({
    mutationFn: (req) => authApi.createCustomRole(req),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.allRoles() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.auth.roles() })
    },
  })
}

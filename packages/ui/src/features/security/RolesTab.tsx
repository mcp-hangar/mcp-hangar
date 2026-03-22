import { useState } from 'react'
import { useAllRoles, useCreateRole, useDeleteRole, useUpdateRole } from './hooks/useRoles'
import { EmptyState, LoadingSpinner } from '../../components/ui'
import type { AllRole, CreateCustomRoleRequest } from '../../types/auth'

// ---- Create / Edit Role modal -----------------------------------------------

interface RoleFormModalProps {
  role?: AllRole
  onClose: () => void
  onSaved: () => void
}

function RoleFormModal({ role, onClose, onSaved }: RoleFormModalProps): JSX.Element {
  const isEdit = !!role
  const [roleName, setRoleName] = useState(role?.name ?? '')
  const [description, setDescription] = useState(role?.description ?? '')
  const [permissionsText, setPermissionsText] = useState(role?.permissions.join('\n') ?? '')
  const [error, setError] = useState<string | null>(null)

  const createMutation = useCreateRole()
  const updateMutation = useUpdateRole()

  function handleSubmit(ev: React.FormEvent): void {
    ev.preventDefault()
    const permissions = permissionsText
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)

    if (!isEdit) {
      if (!roleName.trim()) {
        setError('Role name is required.')
        return
      }
      setError(null)
      const req: CreateCustomRoleRequest = {
        role_name: roleName.trim(),
        description: description.trim() || undefined,
        permissions: permissions.length > 0 ? permissions : undefined,
      }
      createMutation.mutate(req, {
        onSuccess: () => {
          onSaved()
          onClose()
        },
        onError: (e) => setError(e.message),
      })
    } else {
      setError(null)
      updateMutation.mutate(
        {
          roleName: role.name,
          req: {
            permissions,
            description: description.trim() || undefined,
          },
        },
        {
          onSuccess: () => {
            onSaved()
            onClose()
          },
          onError: (e) => setError(e.message),
        }
      )
    }
  }

  const isPending = createMutation.isPending || updateMutation.isPending

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
        <h3 className="text-base font-semibold text-gray-900">
          {isEdit ? `Edit Role: ${role.name}` : 'Create Custom Role'}
        </h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          {!isEdit && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Role Name</label>
              <input
                type="text"
                value={roleName}
                onChange={(e) => setRoleName(e.target.value)}
                className="w-full border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="custom-operator"
              />
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Human-readable description"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Permissions (one per line)</label>
            <textarea
              value={permissionsText}
              onChange={(e) => setPermissionsText(e.target.value)}
              rows={5}
              className="w-full border border-gray-300 rounded-md px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="provider:start:*&#10;provider:stop:*"
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending}
              className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {isPending ? 'Saving...' : isEdit ? 'Update' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---- Delete confirmation dialog ---------------------------------------------

interface DeleteRoleDialogProps {
  roleName: string
  onClose: () => void
  onDeleted: () => void
}

function DeleteRoleDialog({ roleName, onClose, onDeleted }: DeleteRoleDialogProps): JSX.Element {
  const deleteMutation = useDeleteRole()

  function handleDelete(): void {
    deleteMutation.mutate(roleName, {
      onSuccess: () => {
        onDeleted()
        onClose()
      },
    })
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-6 space-y-4">
        <h3 className="text-base font-semibold text-gray-900">Delete Role</h3>
        <p className="text-sm text-gray-600">
          Are you sure you want to delete the role <span className="font-medium text-gray-900">{roleName}</span>? This
          action cannot be undone.
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
            className="px-4 py-1.5 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50"
          >
            {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---- Role card with expandable permissions ----------------------------------

interface RoleCardProps {
  role: AllRole
  onEdit: (role: AllRole) => void
  onDelete: (roleName: string) => void
}

function RoleCard({ role, onEdit, onDelete }: RoleCardProps): JSX.Element {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="text-sm font-medium text-gray-900 hover:text-blue-600 text-left"
        >
          {role.name}
        </button>
        <div className="flex items-center gap-1.5">
          {role.is_builtin ? (
            <span className="text-xs bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2 py-0.5">
              builtin
            </span>
          ) : (
            <>
              <button type="button" onClick={() => onEdit(role)} className="text-xs text-blue-600 hover:underline">
                Edit
              </button>
              <button
                type="button"
                onClick={() => onDelete(role.name)}
                className="text-xs text-red-600 hover:underline"
              >
                Delete
              </button>
            </>
          )}
        </div>
      </div>
      {role.description && <p className="text-xs text-gray-500">{role.description}</p>}
      <p className="text-xs text-gray-400">
        {role.permissions_count} permission{role.permissions_count !== 1 ? 's' : ''}
      </p>
      {expanded && role.permissions.length > 0 && (
        <div className="border-t border-gray-100 pt-2 mt-1">
          <ul className="space-y-0.5">
            {role.permissions.map((perm) => (
              <li key={perm} className="text-xs font-mono text-gray-600">
                {perm}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ---- RolesTab ---------------------------------------------------------------

export function RolesTab(): JSX.Element {
  const { data, isLoading } = useAllRoles()
  const [showCreate, setShowCreate] = useState(false)
  const [editRole, setEditRole] = useState<AllRole | null>(null)
  const [deleteRoleName, setDeleteRoleName] = useState<string | null>(null)

  const roles = data?.roles ?? []

  return (
    <div className="space-y-4">
      {showCreate && <RoleFormModal onClose={() => setShowCreate(false)} onSaved={() => setShowCreate(false)} />}
      {editRole && (
        <RoleFormModal role={editRole} onClose={() => setEditRole(null)} onSaved={() => setEditRole(null)} />
      )}
      {deleteRoleName && (
        <DeleteRoleDialog
          roleName={deleteRoleName}
          onClose={() => setDeleteRoleName(null)}
          onDeleted={() => setDeleteRoleName(null)}
        />
      )}

      <div className="flex items-center justify-between">
        <div className="text-sm text-gray-500">
          {data ? (
            <>
              {data.total} role{data.total !== 1 ? 's' : ''} ({data.builtin_count} builtin, {data.custom_count} custom)
            </>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          Create Custom Role
        </button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <LoadingSpinner size={28} />
        </div>
      ) : roles.length === 0 ? (
        <EmptyState message="No roles found." />
      ) : (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {roles.map((role) => (
            <RoleCard key={role.name} role={role} onEdit={setEditRole} onDelete={setDeleteRoleName} />
          ))}
        </div>
      )}
    </div>
  )
}

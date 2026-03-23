import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAllRoles, useCreateRole, useDeleteRole, useUpdateRole } from './hooks/useRoles'
import { EmptyState, LoadingSpinner } from '../../components/ui'
import { staggerContainer, staggerItem, modalVariants, overlayVariants, expandVariants } from '../../lib/animations'
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
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <motion.div
        variants={overlayVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        className="absolute inset-0 bg-overlay"
        onClick={onClose}
      />
      <motion.div
        variants={modalVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        className="relative bg-surface rounded-xl shadow-xl w-full max-w-md p-6 space-y-4"
      >
        <h3 className="text-base font-semibold text-text-primary">
          {isEdit ? `Edit Role: ${role.name}` : 'Create Custom Role'}
        </h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          {!isEdit && (
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">Role Name</label>
              <input
                type="text"
                value={roleName}
                onChange={(e) => setRoleName(e.target.value)}
                className="w-full border border-border-strong rounded-lg bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
                placeholder="custom-operator"
              />
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Description</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full border border-border-strong rounded-lg bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="Human-readable description"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Permissions (one per line)</label>
            <textarea
              value={permissionsText}
              onChange={(e) => setPermissionsText(e.target.value)}
              rows={5}
              className="w-full border border-border-strong rounded-lg bg-surface px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="provider:start:*&#10;provider:stop:*"
            />
          </div>
          {error && <p className="text-sm text-danger">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-1.5 text-sm border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending}
              className="px-4 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors"
            >
              {isPending ? 'Saving...' : isEdit ? 'Update' : 'Create'}
            </button>
          </div>
        </form>
      </motion.div>
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
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <motion.div
        variants={overlayVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        className="absolute inset-0 bg-overlay"
        onClick={onClose}
      />
      <motion.div
        variants={modalVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        className="relative bg-surface rounded-xl shadow-xl w-full max-w-sm p-6 space-y-4"
      >
        <h3 className="text-base font-semibold text-text-primary">Delete Role</h3>
        <p className="text-sm text-text-muted">
          Are you sure you want to delete the role <span className="font-medium text-text-primary">{roleName}</span>?
          This action cannot be undone.
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 text-sm border border-border-strong rounded-lg hover:bg-surface-secondary transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
            className="px-4 py-1.5 text-sm bg-danger text-white rounded-lg hover:bg-danger-hover disabled:opacity-50 transition-colors"
          >
            {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
          </button>
        </div>
      </motion.div>
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
    <div className="bg-surface border border-border rounded-xl p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="text-sm font-medium text-text-primary hover:text-accent text-left"
        >
          {role.name}
        </button>
        <div className="flex items-center gap-1.5">
          {role.is_builtin ? (
            <span className="text-xs bg-accent-surface text-accent-text border border-accent rounded-full px-2 py-0.5">
              builtin
            </span>
          ) : (
            <>
              <button type="button" onClick={() => onEdit(role)} className="text-xs text-accent hover:underline">
                Edit
              </button>
              <button type="button" onClick={() => onDelete(role.name)} className="text-xs text-danger hover:underline">
                Delete
              </button>
            </>
          )}
        </div>
      </div>
      {role.description && <p className="text-xs text-text-muted">{role.description}</p>}
      <p className="text-xs text-text-faint">
        {role.permissions_count} permission{role.permissions_count !== 1 ? 's' : ''}
      </p>
      <AnimatePresence>
        {expanded && role.permissions.length > 0 && (
          <motion.div
            variants={expandVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="border-t border-surface-tertiary pt-2 mt-1 overflow-hidden"
          >
            <ul className="space-y-0.5">
              {role.permissions.map((perm) => (
                <li key={perm} className="text-xs font-mono text-text-muted">
                  {perm}
                </li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
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
        <div className="text-sm text-text-muted">
          {data ? (
            <>
              {data.total} role{data.total !== 1 ? 's' : ''} ({data.builtin_count} builtin, {data.custom_count} custom)
            </>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors"
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
        <motion.div
          variants={staggerContainer}
          initial="hidden"
          animate="visible"
          className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
        >
          {roles.map((role) => (
            <motion.div key={role.name} variants={staggerItem}>
              <RoleCard role={role} onEdit={setEditRole} onDelete={setDeleteRoleName} />
            </motion.div>
          ))}
        </motion.div>
      )}
    </div>
  )
}

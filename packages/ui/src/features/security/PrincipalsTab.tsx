import { useState } from 'react'
import { motion } from 'framer-motion'
import { usePrincipals, usePermissions, useCheckPermission, useAssignRole, useRevokeRole } from './hooks/usePrincipals'
import { useAllRoles } from './hooks/useRoles'
import { EmptyState, LoadingSpinner } from '../../components/ui'
import { modalVariants, overlayVariants } from '../../lib/animations'
import type { CheckPermissionResponse, Principal } from '../../types/auth'

// ---- Assign Role modal ------------------------------------------------------

interface AssignRoleModalProps {
  onClose: () => void
}

function AssignRoleModal({ onClose }: AssignRoleModalProps): JSX.Element {
  const { data: rolesData } = useAllRoles()
  const assignMutation = useAssignRole()

  const [principalId, setPrincipalId] = useState('')
  const [roleName, setRoleName] = useState('')
  const [scope, setScope] = useState('')
  const [error, setError] = useState<string | null>(null)

  const roles = rolesData?.roles ?? []

  function handleSubmit(ev: React.FormEvent): void {
    ev.preventDefault()
    if (!principalId.trim() || !roleName) {
      setError('Principal ID and Role are required.')
      return
    }
    setError(null)
    assignMutation.mutate(
      {
        principal_id: principalId.trim(),
        role_name: roleName,
        scope: scope.trim() || undefined,
      },
      {
        onSuccess: () => onClose(),
        onError: (e) => setError(e.message),
      }
    )
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
        className="relative bg-surface rounded-xl shadow-xl w-full max-w-md p-6 space-y-4"
      >
        <h3 className="text-base font-semibold text-text-primary">Assign Role</h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Principal ID</label>
            <input
              type="text"
              value={principalId}
              onChange={(e) => setPrincipalId(e.target.value)}
              className="w-full border border-border-strong rounded-lg bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="user:alice"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Role</label>
            <select
              value={roleName}
              onChange={(e) => setRoleName(e.target.value)}
              className="w-full border border-border-strong rounded-lg bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="">Select a role...</option>
              {roles.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Scope (optional)</label>
            <input
              type="text"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="w-full border border-border-strong rounded-lg bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="global"
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
              disabled={assignMutation.isPending}
              className="px-4 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors"
            >
              {assignMutation.isPending ? 'Assigning...' : 'Assign'}
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  )
}

// ---- Permission Check tool --------------------------------------------------

function PermissionCheckTool(): JSX.Element {
  const { data: permissionsData } = usePermissions()
  const checkMutation = useCheckPermission()

  const [principalId, setPrincipalId] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [action, setAction] = useState('')
  const [resourceId, setResourceId] = useState('')
  const [result, setResult] = useState<CheckPermissionResponse | null>(null)

  const permissionResources = permissionsData?.permissions ?? []

  const selectedResource = permissionResources.find((p) => p.resource_type === resourceType)
  const availableActions = selectedResource?.actions ?? []

  function handleCheck(ev: React.FormEvent): void {
    ev.preventDefault()
    if (!principalId.trim() || !resourceType || !action) return
    checkMutation.mutate(
      {
        principal_id: principalId.trim(),
        resource_type: resourceType,
        action,
        resource_id: resourceId.trim() || undefined,
      },
      {
        onSuccess: (data) => setResult(data),
      }
    )
  }

  return (
    <div className="bg-surface-secondary border border-border rounded-xl p-4 space-y-3">
      <h4 className="text-sm font-semibold text-text-secondary">Permission Check</h4>
      <form onSubmit={handleCheck} className="flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[160px]">
          <label className="block text-xs font-medium text-text-muted mb-1">Principal</label>
          <input
            type="text"
            value={principalId}
            onChange={(e) => setPrincipalId(e.target.value)}
            className="w-full border border-border-strong rounded-lg bg-surface px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
            placeholder="user:alice"
          />
        </div>
        <div className="min-w-[120px]">
          <label className="block text-xs font-medium text-text-muted mb-1">Resource Type</label>
          <select
            value={resourceType}
            onChange={(e) => {
              setResourceType(e.target.value)
              setAction('')
            }}
            className="w-full border border-border-strong rounded-lg bg-surface px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="">--</option>
            {permissionResources.map((p) => (
              <option key={p.resource_type} value={p.resource_type}>
                {p.resource_type}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-[100px]">
          <label className="block text-xs font-medium text-text-muted mb-1">Action</label>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="w-full border border-border-strong rounded-lg bg-surface px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
            disabled={!resourceType}
          >
            <option value="">--</option>
            {availableActions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-[120px]">
          <label className="block text-xs font-medium text-text-muted mb-1">Resource ID</label>
          <input
            type="text"
            value={resourceId}
            onChange={(e) => setResourceId(e.target.value)}
            className="w-full border border-border-strong rounded-lg bg-surface px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
            placeholder="* (any)"
          />
        </div>
        <button
          type="submit"
          disabled={checkMutation.isPending || !principalId.trim() || !resourceType || !action}
          className="px-3 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors"
        >
          {checkMutation.isPending ? 'Checking...' : 'Check'}
        </button>
      </form>

      {result && (
        <div className="flex items-center gap-2 pt-1">
          <span
            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
              result.allowed ? 'bg-success-surface text-success-text' : 'bg-danger-surface text-danger-text'
            }`}
          >
            {result.allowed ? 'Allowed' : 'Denied'}
          </span>
          {result.granted_by_role && (
            <span className="text-xs text-text-muted">
              Granted by role: <span className="font-medium">{result.granted_by_role}</span>
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// ---- Principal row ----------------------------------------------------------

interface PrincipalRowProps {
  principal: Principal
  onRevoke: (principalId: string, roleName: string) => void
  isRevoking: boolean
}

function PrincipalRow({ principal, onRevoke, isRevoking }: PrincipalRowProps): JSX.Element {
  return (
    <tr className="border-b border-surface-tertiary hover:bg-surface-secondary">
      <td className="py-2 px-3 text-sm text-text-primary">{principal.principal_id}</td>
      <td className="py-2 px-3">
        <div className="flex flex-wrap gap-1">
          {principal.roles.map((role) => (
            <span
              key={role}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-accent-surface text-accent-text rounded border border-accent"
            >
              {role}
              <button
                type="button"
                onClick={() => onRevoke(principal.principal_id, role)}
                disabled={isRevoking}
                className="text-accent-text hover:text-danger leading-none disabled:opacity-40"
                title={`Revoke ${role}`}
              >
                &times;
              </button>
            </span>
          ))}
        </div>
      </td>
      <td className="py-2 px-3 text-sm text-text-faint">{principal.roles.length}</td>
    </tr>
  )
}

// ---- PrincipalsTab ----------------------------------------------------------

export function PrincipalsTab(): JSX.Element {
  const { data, isLoading } = usePrincipals()
  const revokeMutation = useRevokeRole()
  const [showAssign, setShowAssign] = useState(false)

  const principals = data?.principals ?? []

  function handleRevoke(principalId: string, roleName: string): void {
    revokeMutation.mutate({ principalId, roleName })
  }

  return (
    <div className="space-y-4">
      {showAssign && <AssignRoleModal onClose={() => setShowAssign(false)} />}

      <div className="flex items-center justify-between">
        <div className="text-sm text-text-muted">
          {data ? `${data.total} principal${data.total !== 1 ? 's' : ''} with role assignments` : null}
        </div>
        <button
          type="button"
          onClick={() => setShowAssign(true)}
          className="px-3 py-1.5 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover transition-colors"
        >
          Assign Role
        </button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <LoadingSpinner size={28} />
        </div>
      ) : principals.length === 0 ? (
        <EmptyState message="No principals with role assignments found." />
      ) : (
        <div className="bg-surface rounded-xl border border-border overflow-hidden shadow-xs">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">
                  Principal
                </th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">Roles</th>
                <th className="text-[11px] font-medium text-text-muted uppercase tracking-wider py-2.5 px-3">Count</th>
              </tr>
            </thead>
            <tbody>
              {principals.map((p) => (
                <PrincipalRow
                  key={p.principal_id}
                  principal={p}
                  onRevoke={handleRevoke}
                  isRevoking={revokeMutation.isPending}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <PermissionCheckTool />
    </div>
  )
}

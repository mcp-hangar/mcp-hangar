### Removing a line from `auth.role_assignments` now revokes its grant

When the server boots with auth enabled, a grant that `auth.role_assignments`
applied and no longer lists is revoked. This only matters with a durable auth
store (`sqlite`, `postgresql`, `event_sourcing`); the `memory` store forgot
everything at a restart already. `mcp-hangar auth bootstrap-admin` applies the
file's lines as before but never revokes anything.

- Grants made through `POST /api/auth/roles/assign` or `bootstrap-admin` are
  never revoked by a boot, even when the same role is also listed in the file:
  the API call or the bootstrap claim takes the grant over from the file, in
  whichever order the two happened.
- Grants written before this release record no assigner, so a boot cannot tell
  a config line from an API call. Each one the file still lists is adopted by
  the file: removing that line later revokes it. That includes an old API grant
  that happens to duplicate a line, so to keep such a grant independent of the
  file, assign it again through the API before removing the line.
- Each pre-release grant the file does not list is kept, and logged at every
  boot as `untracked_role_assignment_not_in_config` with its principal, role and
  scope. If it came from a line you deleted, revoke it with
  `DELETE /api/auth/roles/revoke` (body: `principal_id`, `role_name`, `scope`).
  If it should stay, assign it again through `POST /api/auth/roles/assign`, or
  list it in the file, and the warning stops.
- Every process that boots the server against the same durable store
  reconciles against its own file. A second process with auth enabled and a
  different `auth.role_assignments` -- a replica mid-rollout on shared
  PostgreSQL, or a development `serve` pointed at the production SQLite file --
  revokes the file grants its list lacks, and the other process re-grants them
  only at its next boot. Give every such process the same list.
- On `event_sourcing`, a change of who owns a grant -- the file adopting an
  old grant, or the API taking one over from the file -- is recorded as a new
  `RoleAssigned` event and published like any other, so the first boot after
  the upgrade emits one per adopted grant. The SQL stores update the row in
  place and emit nothing.
- `IRoleStore` has a new abstract method, `list_role_assignments()`, returning
  one `{"principal_id", "role_name", "scope", "assigned_by"}` per assignment.
  An out-of-tree role store must implement it, or it no longer instantiates.

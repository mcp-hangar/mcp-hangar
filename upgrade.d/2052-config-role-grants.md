### Removing a line from `auth.role_assignments` now revokes its grant

At each boot -- and each `mcp-hangar auth bootstrap-admin` run, which loads the
same config -- a grant that `auth.role_assignments` applied and no longer lists
is revoked. This only matters with a durable auth store (`sqlite`,
`postgresql`, `event_sourcing`); the `memory` store forgot everything at a
restart already.

- Grants made through `POST /api/auth/roles/assign` or `bootstrap-admin` are
  never revoked by a boot, even when the same role is also listed in the file.
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
- Replicas sharing one PostgreSQL database must run the same
  `auth.role_assignments`. During a rolling change of that list, a replica
  still on the old file re-grants a removed line when it restarts, and the next
  boot on the new file revokes it again.

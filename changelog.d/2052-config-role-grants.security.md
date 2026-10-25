**security:** a line removed from `auth.role_assignments` now loses its role at
the next boot. Every boot applied the file's entries and nothing ever took one
away, so with the `sqlite`, `postgresql` or `event_sourcing` auth store a
principal kept a role after its line was deleted from config.yaml, until
someone revoked it through `DELETE /api/auth/roles/revoke`. Grants the file
applies are now recorded against the reserved assigner
`config/auth.role_assignments`, which no principal id can spell, and a server
boot revokes the ones the file no longer lists. Grants made through the API or
`mcp-hangar auth bootstrap-admin` take over a matching file grant and are never
revoked by a boot; `bootstrap-admin` itself applies the file add-only and
revokes nothing. Grants written before this release record no assigner; one the
file lists is adopted by it, and one it does not list is kept and named in an
`untracked_role_assignment_not_in_config` warning at every boot. The SQLite and
PostgreSQL stores now also record `assigned_by` for every assignment, which
they used to leave empty, and `IRoleStore` gains `list_role_assignments()`

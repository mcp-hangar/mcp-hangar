**core:** a group's `access.prompt` / `access.resource` policy was registered and
never read, so a declared deny enforced nothing on the prompts and resources
surfaces (fail-open). `prompt_proxy._upstream_ids` collapses a group member to
its group id before any check runs, and `is_governed_allowed` only mapped the
other direction -- member id to group -- so it asked the resolver with
`group_id=None` and the group's policy was never merged. Both spellings now
resolve to the group scope, the way `tools:` on the same group always did

### An invalid access policy refuses the configuration

A `tools:` access policy (`allow_list` / `deny_list` / `approval_list`) on a
server, a group, a group member or a `tool_access.member` tenant entry, and an
`access:` block, is now refused when one of its fields is invalid: an
`approval_timeout_seconds` that is not a positive integer, an empty or
non-string pattern, a whitespace-only `approval_channel`, or a list that is not
a list.

Before, the gateway logged `invalid_tools_access_config` (or the group, member,
tenant or `access` variant) at warning and booted with **no policy** for that
scope. Now the boot fails, and a reload is refused with the previous policy kept
in force, with a `ConfigurationError` naming the scope and the field, for
example:

```text
Invalid tools access policy for mcp_server 'calc': Invalid approval_timeout_seconds: 0
```

A configuration that booted with one of those warnings in its log will not boot
on this release. Fix the field the error names. A policy you meant to have no
effect is removed by deleting the block, not by leaving it invalid.

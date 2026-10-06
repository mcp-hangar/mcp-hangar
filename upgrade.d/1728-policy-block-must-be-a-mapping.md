### A tool access policy block that is not a mapping refuses the configuration

A policy block that is present must now be a mapping. This covers `tools:` on a
server, a group or a group member, `access:` and each `access.<kind>`, and
`tool_access:`, `tool_access.member` and each `tool_access.member.<tenant>`
entry on a server. On a server, and on a group member that defines its server
inline, `tools:` may still be a list of tool schemas, and each item must be a
mapping.

Before, a block of any other shape was skipped and the gateway booted with no
policy for that scope: `tools: add` left every tool allowed. A key with no value
(YAML null) was skipped the same way. Now the boot fails, and a reload is
refused with the previous policy kept in force, with a `ConfigurationError`
naming the scope, for example:

```text
Invalid tools access policy for group 'pool': expected a mapping, got str 'add'
```

Old form:

```yaml
tools: add
access:
  prompt:
```

New form:

```yaml
tools:
  deny_list: [add]
# or remove the key, or write `access: {}` / `prompt: {}` for no policy
```

`tools: [add]` on a server was read as a tool schema list and stopped the boot
with an `AttributeError`; it is now a `ConfigurationError` that says a policy is
a mapping. A list under `tools:` on a group, or on a member that names a server
declared under `mcp_servers`, used to be ignored and is now refused.
`hangar_load` takes no policy block, only the `allow_tools`, `deny_tools` and
`approval_tools` lists, which #1718 already refuses when they are not lists.

### A tool access list written as a string or a mapping refuses the configuration

`allow_list`, `deny_list` and `approval_list` in a `tools:` policy (on a
server, a group, a group member or a `tool_access.member` tenant entry) and in
an `access:` block must now be lists.

Before, a string was split into its characters and a mapping was read as its
keys, and the gateway booted. `deny_list: add` became the patterns `a`, `d`,
`d`, so `add` was **allowed**. Now the boot fails, and a reload is refused with
the previous policy kept in force, with a `ConfigurationError` naming the scope
and the field, for example:

```text
Invalid tools access policy for mcp_server 'calc': Invalid deny_list: expected a list of patterns, got str 'add'
```

Old form:

```yaml
tools:
  deny_list: add
```

New form:

```yaml
tools:
  deny_list: [add]
```

A configuration that used the old form booted with a different policy from
the one written; it will not boot on this release until the list is fixed.
`hangar_load` likewise refuses `allow_tools`, `deny_tools` or `approval_tools`
that is not a list, before anything is installed.

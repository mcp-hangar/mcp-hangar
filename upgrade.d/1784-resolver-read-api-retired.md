### `ToolAccessResolver` no longer answers access questions

This affects only code that imports `mcp_hangar.domain.services` directly. A
gateway, its configuration and its REST and MCP surfaces behave as before.

The read methods of `ToolAccessResolver` are gone, and
`mcp_hangar.domain.services.decide` replaces them. It takes a typed target and
caller from `mcp_hangar.domain.value_objects.access`:

- `resolver.is_tool_allowed(server, tool, member_id=tenant)` and
  `resolver.is_allowed(server, tool, member_id=tenant)` become
  `decide(Target.server(server), tool, Caller(tenant_id=tenant)).allowed`.
- On a group, `group_id=group, member_server_id=member` becomes
  `Target.group(group, member_server=member)`. A prompt or a resource passes
  `kind=` to `Target.server` or `Target.group`.
- `resolver.filter_tools(...)` and `resolver.filter_tool_dicts(...)` become one
  `decide()` per name.
- `resolver.resolve_effective_policy(...)`, when read for an approval list,
  becomes `decide(...).approval_policy`.

There is no public replacement for reading the merged policy itself. `decide()`
also applies withdrawals, which the old methods did not, and it needs the
withdrawal overlay that server bootstrap wires; outside a bootstrapped server,
pass `withdrawals=`.

The registration API (`set_*_policy`, `remove_*_policy`,
`adopt_config_policies`, `set_topology_mode`, `iter_registered_policies`,
`get_policy_summary`) is unchanged.

### a front door serves only the tools `tool_projection.flat` names

A front door (`tool_access.mode: front_door`) used to project every tool of
every upstream under its own name. It now projects only the tools each server
and group lists in `tool_projection.flat`, and a front door with a server or
group that has no `flat` key does not start:

```text
mcp_servers.github is served on a front door (tool_access.mode: front_door) and has not chosen which of its tools are projected under their own names. That surface must be chosen explicitly: ...
```

Every existing front-door deployment must add the key to every entry in
`mcp_servers`, groups included. A reload of a file without it is refused, and
the gateway keeps serving what it served. `egress` deployments change nothing;
`flat` under `egress` is refused, because egress projects no tool under its own
name.

What the front door served before, in the new shape, is every tool the server
serves:

```yaml
mcp_servers:
  github:
    mode: subprocess
    command: [uvx, mcp-server-github]
    tool_projection:
      flat: [search_repositories, get_file_contents, create_issue]  # every tool tools/list showed
tool_access:
  mode: front_door
```

There is no `flat: all` and no wildcard: each tool is named. To list the tools a
server serves today, do one of these:

- Before upgrading, read `tools/list` from the running front door, or
  `GET /api/mcp_servers/<id>/tools` (`jq -r '.tools[].name'`).
- After upgrading, write `flat: []` on each entry so the gateway starts, run
  `mcp-hangar pin --write`, and copy the tool names it writes under
  `tool_projection.pins` into `flat`. Leave out the ones no client needs.

For a group, list the tools under the group: a member of one group is projected
by its group's list. A name the entry's own `tools:` does not make visible (not
in its `tools:` list, or refused by its `tools:` policy) is refused at load, and
the message names both keys.

What a tool that is not listed becomes:

- It is not in `tools/list`, and a call of it answers `-32601`, as a name that
  does not exist does.
- Until discovery on demand ships (mcp-hangar#1373) it is unreachable on a front
  door by any path: `hangar_call` is not served there. Name every tool a client
  must call.
- `flat: []` is still a front door, not `egress`: it serves only callers with a
  tenant, and still serves the upstream's prompts, resources and subscriptions.

A server registered over `POST /api/mcp_servers` or by discovery has no `flat`
key, so a front door now projects none of its tools; on `egress` it is
unchanged. Who may call a projected tool is unchanged (ADR-028): `tool:invoke`
from the configured authorizer, narrowed by tool-access policy.

`mcp-hangar init` writes `flat` with the tools its smoke test saw. With
`--skip-test` it writes `flat: []`, and the generated config serves no upstream
tool until you name them. `mcp-hangar add` writes no `flat` key: add it to the
new server before the next start.

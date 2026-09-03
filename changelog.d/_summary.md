This release makes a first run produce a verdict on a laptop. `mcp-hangar init` writes a
configuration that enforces something -- the upstreams' own tool names through
`front_door`, a declared caller over stdio, and a digest pin for every tool your servers
serve -- and `mcp-hangar pin` computes, writes and checks those pins, which until now
existed only as a thing the gate compared against and no command produced.

**Two behaviours change only if you opt in.** A stdio session gets an identity when you
declare one under `auth.stdio.principal`, and `init` writes the new shape when you run it.
An existing configuration with neither is served exactly as it was in 2.17.1.

**One changes for everyone**, and it is cosmetic: the CLI stopped printing the internal
spelling `mcp_server` at people. Help text now says "MCP server"; `init --mcp_servers` is
`init --servers`, with the old spelling still accepted.

To adopt the new enforcement on a configuration you already have, run
`mcp-hangar pin --write` and add the `tool_access` and `auth.stdio.principal` blocks --
see [Upgrading](https://github.com/mcp-hangar/mcp-hangar/blob/main/UPGRADE.md).

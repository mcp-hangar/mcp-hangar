**core:** `mcp-hangar init` now writes a configuration that enforces something. The
generated file gains `tool_access.mode: front_door` (your client sees the upstreams' own
tool names, not Hangar's meta-API), an `auth.stdio.principal` block so that projection is
reachable over stdio at all, and a digest pin for every tool the smoke test saw --
captured during that run, while the servers are already up. `--skip-test` therefore also
means no pins: an unverified pin would refuse every call to a tool nobody digested. The
summary panel now says which of those happened instead of defaulting to "All passed".

`init` also writes the clients people actually use: Claude Code (`~/.claude.json`,
`./.mcp.json`), Cursor (`~/.cursor/mcp.json`, `./.cursor/mcp.json`) and Claude Desktop,
detected automatically or named with `--client` (`--skip-claude` still works as
`--skip-clients`). The Hangar entry is now **merged** into a client's `mcpServers` rather
than replacing it -- the previous writer discarded every other server in that file.

**core:** the default `starter` bundle could not start. `init` preferred a PyPI
distribution derived from each npm package name, and seven of the ten names that produced
were wrong: `mcp-server-filesystem`, `mcp-server-github`, `mcp-server-slack` and
`mcp-server-google-maps` do not exist, `mcp-server-memory` ships no executable, and
`mcp-server-postgres` and `mcp-server-brave-search` belong to other people -- uvx would
have fetched and run a stranger's code under an official-looking name. Only `fetch` and
`git` are published by Anthropic from `modelcontextprotocol/servers`; the rest now use
their real npm packages. Existing configurations are untouched; a config already pointing
at one of those names keeps doing so until you regenerate it.

**core:** the generated config no longer writes a `health_check:` block. Nothing has ever
read it -- the worker takes its interval from a constant -- so every generated config
logged `unknown_config_key`, and an operator who tuned `interval_s` tuned nothing.

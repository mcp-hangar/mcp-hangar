**core:** the CLI stopped saying `mcp_server` at people. `mcp-hangar --help` described "a
Production-grade MCP mcp_server platform"; `status` offered to "Show status of all
mcp_servers"; `add` would "Add a mcp_server". That spelling is an internal identifier left
over from the `provider` rename, and it appeared in the root description, in nine command
help texts and in `status`'s `Usage:` line. All of it now reads "MCP server(s)", and the
root line says what Hangar is: *the policy enforcement plane for your MCP servers*.

`init --mcp_servers` is now `init --servers`, with the old spelling kept as an alias --
renaming a flag people have in scripts is a breaking change; renaming the one they read is
not. `mcp_servers` stays where it is a name rather than prose: the configuration section
and every identifier in the code.

A test walks the real Typer app and reads what each command renders, so a help string that
reintroduces the identifier fails the build rather than shipping.

**core:** the sdist no longer carries `.grimp_cache`, `.import_linter_cache`,
`.clusterfuzzlite` or `coverage.json` -- artefacts of having built the project, not inputs
for building it (2.6 MB to 2.3 MB). `scripts/` and `examples/` stay: the test suite imports
`scripts.dump_api_routes`, and the quickstart points at `examples/rugpull/`.

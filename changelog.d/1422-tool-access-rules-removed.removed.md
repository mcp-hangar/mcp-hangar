**core:** `tool_access.rules` is no longer an accepted config key. The schema
listed it beside `tool_access.mode`, but nothing ever read it, so a `rules:`
block passed `mcp-hangar config check` and `HANGAR_CONFIG_STRICT=1`, loaded
without a warning and restricted no tool. A config that still sets it, from a
file or from `bootstrap(config_dict=...)`, now loads and logs
`unknown_config_key` saying the key was never read; `HANGAR_CONFIG_STRICT=1`
and `mcp-hangar config check` refuse it. Delete the key. Tool access is still
set by the `tools:` allow and deny lists of a server, a group or a group
member. See `UPGRADE.md`

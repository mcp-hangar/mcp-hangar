**core:** a configuration passed to `bootstrap(config_dict=...)` is now applied
the same way as the same document read from a file. The dict path skipped
`tool_access.mode`, `execution`, `headers.param_validation`, `resource_links`,
`interceptors` and `ui_resources`, and it skipped the config schema check. So a
dict that asked for `front_door` came up in egress mode, and a dict's parameter
validators never ran, with nothing logged. Both paths now go through one
function, `apply_configuration`. Embedders and test harnesses that pass a dict
now get every setting they pass, validators included. An unknown key in a dict
now logs a warning, and `HANGAR_CONFIG_STRICT` makes it refuse to start, as for
a file. A dict is now the whole configuration: it is no longer merged over
`MCP_CONFIG` or `./config.yaml`. A dict with no `mcp_servers` section is refused
the way a file is, where it used to boot the built-in example server. A dict
that enables `config_reload`, or a call that passes both `config_path` and
`config_dict`, is also refused. See `UPGRADE.md`

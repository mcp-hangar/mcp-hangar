**core:** the config schema drifted from the readers in both directions.
A documented per-server `max_concurrency` warned as `unknown_config_key`,
failed `mcp-hangar config check`, and was refused under
`HANGAR_CONFIG_STRICT=1` even though the limit demonstrably applied; it is
now in `SERVER_SPEC_KEYS`. `working_dir` sat in the schema with no reader --
a config carrying it validated cleanly while the key silently did nothing --
and is now rejected like any other unread key

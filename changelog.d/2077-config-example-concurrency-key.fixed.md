**core:** `config.yaml.example` now sets `execution.default_mcp_server_concurrency`
instead of the pre-rename `default_provider_concurrency`, which nothing reads. The
shipped example failed `mcp-hangar config check` and was refused under
`HANGAR_CONFIG_STRICT=1`, and editing that line changed nothing. A test now runs
`config check` on the example

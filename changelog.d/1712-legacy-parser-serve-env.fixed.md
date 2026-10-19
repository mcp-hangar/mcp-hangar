**cli:** `python -m mcp_hangar.server` (the legacy `parse_args` entry point) now reads the environment the way
`mcp-hangar serve` does: `MCP_MODE` accepts `true`/`1`/`yes`/`on` as well as `http`, `MCP_JSON_LOGS=1` turns JSON logs
on, and a non-integer `MCP_HTTP_PORT` is a usage error (exit 2) instead of a traceback.

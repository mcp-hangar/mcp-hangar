### `MCP_COMPLIANCE_OUTPUT` without `MCP_COMPLIANCE_FORMAT` refuses startup

A gateway with `MCP_COMPLIANCE_OUTPUT` set and `MCP_COMPLIANCE_FORMAT` unset used to start with no SIEM export at all. It now refuses to start. Set `MCP_COMPLIANCE_FORMAT` (`cef`, `leef`, `jsonlines` or `syslog`) to export to that path, or unset `MCP_COMPLIANCE_OUTPUT` if you meant no export.

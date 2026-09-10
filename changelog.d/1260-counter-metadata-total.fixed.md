**core:** `/metrics` put every counter's `# HELP` and `# TYPE` on a family
that has no samples. The header lines said `mcp_hangar_tool_calls` while the
samples said `mcp_hangar_tool_calls_total`, so Prometheus stored the type and
help text under a name that never returns a value and nothing under the one
anyone queries: Grafana's metric browser showed all 48 counters untyped and
undocumented, and `/api/v1/metadata` had no answer for them. The header lines
now name `..._total`, as `prometheus_client` writes them for text format 0.0.4.
`mcp_hangar_build_info` was split the same way, `# HELP` on `mcp_hangar_build`
and `# TYPE` on `mcp_hangar_build_info`, and now names `_info` in both. No
series is renamed, so dashboards, alerts and `rate()` queries are unaffected

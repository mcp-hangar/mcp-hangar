### A SIEM export the gateway cannot perform refuses startup

Before, an unknown `MCP_COMPLIANCE_FORMAT` logged `unknown_compliance_format`
at warning and the gateway started with **no export**. An
`MCP_COMPLIANCE_OUTPUT` whose directory did not exist, or could not be written,
started too, and every record was logged as an error and dropped.

Now each refuses the boot with a `ConfigurationError` naming the value:

```text
Unknown MCP_COMPLIANCE_FORMAT 'cefx'; expected one of: cef, json-lines, jsonlines, leef, syslog
MCP_COMPLIANCE_OUTPUT '/var/log/hangar/audit.cef' cannot be appended to: [Errno 2] No such file or directory: ...
```

So does a set `MCP_COMPLIANCE_FORMAT` whose exporter cannot be imported, which
used to log `compliance_exporter_unavailable` and start without export. The
format is now matched after trimming whitespace, so `" cef"` is read as `cef`.

A deployment that started with one of those in its log will not start on this
release. Fix the format, create the output directory writable by the gateway's
user, or unset `MCP_COMPLIANCE_OUTPUT` to export to stderr. To run with no SIEM
export, unset `MCP_COMPLIANCE_FORMAT`.

A write that fails after startup does not stop the gateway or refuse calls. It
is counted in `mcp_hangar_compliance_export_failures_total` (labels `format`
and `reason`), and `/health/ready` and `hangar_health` carry a
`compliance_export` field whose `status` is `degraded` until a write succeeds
again; `/health/ready` keeps answering 200. Alert on the counter.

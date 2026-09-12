**core:** OTLP audit log export can now be turned off without turning off
tracing. Set `observability.audit.enabled: false` in the config file, or
`MCP_AUDIT_EXPORT_ENABLED=false` in the environment; the environment variable
wins over the file. The default is `true`, so an OTLP endpoint set explicitly
(`OTEL_EXPORTER_OTLP_ENDPOINT` or `observability.tracing.otlp_endpoint`) still
turns audit export on, as it has since #1318. Switched off, Hangar builds no
audit log pipeline and exports no audit records over OTLP, so the caller
identities they carry are not sent. Trace export, the in-process audit trail
and the compliance feed selected by `MCP_COMPLIANCE_FORMAT` are unaffected.

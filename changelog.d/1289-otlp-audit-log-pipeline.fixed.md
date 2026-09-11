**core:** With the OpenTelemetry SDK installed, audit records for tool calls
and server state changes were dropped. Nothing registered a logger provider,
so the OTLP audit exporter handed every record to the API's placeholder, and
the structured-log fallback was skipped as well. Now, when an OTLP endpoint is
configured, Hangar registers its own logger provider with a batch processor and
an OTLP log exporter, and sends audit records to that endpoint. An endpoint set
only in the config file (`observability.tracing.otlp_endpoint`) now turns audit
export on, as `OTEL_EXPORTER_OTLP_ENDPOINT` already did, and
`observability.tracing.enabled: false` does not turn it off. When another
component registered a logger provider first, Hangar sends its records through
that one and never replaces, flushes or shuts it down. Without the SDK, or with
no provider registered, audit records now go to the structured log instead of
nowhere. Failed export batches are counted in
`mcp_hangar_otlp_audit_export_failures_total`, and shutdown waits at most 5
seconds (`AUDIT_LOG_SHUTDOWN_TIMEOUT_S`) for records still pending. This is
verified as far as Hangar's own export pipeline; that a collector receives the
records is not yet verified end to end

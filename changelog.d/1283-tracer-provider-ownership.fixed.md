**core:** Hangar no longer claims a tracer provider that the host application
registered first. It used to build its own anyway, attach its exporters to it
and log `tracing_initialized`, although the OpenTelemetry API had refused to
register it ("Overriding of current TracerProvider is not allowed"), so those
exporters never received a span. Before that point, or wherever Hangar's own
init never ran, as when it is embedded, it handed out no-op tracers and
propagated no `traceparent`, even under the host's active span. Now Hangar
builds no exporters in that case, logs `tracing_external_provider_in_use`, and
sends its spans and trace context through the host's provider, which it leaves
to the host to shut down. Tracing disabled in configuration, by
`observability.tracing.enabled: false` or `MCP_TRACING_ENABLED=false`, keeps
Hangar's spans and trace context off even when another provider is registered.

With `MCP_TRACING_CONSOLE` on, spans are now written to stderr. They went to
stdout, which on the stdio transport is the JSON-RPC stream.

`shutdown_tracing` now returns within 5 seconds (`TRACING_SHUTDOWN_TIMEOUT_S`)
when the collector is unreachable, instead of waiting out the OTLP export
timeout, and logs `tracing_shutdown_timed_out`; spans not exported by then are
dropped. Calling `init_tracing` after a shutdown now returns False and logs
`tracing_init_refused` with `reason=already_shut_down`, instead of building a
provider that could never be registered

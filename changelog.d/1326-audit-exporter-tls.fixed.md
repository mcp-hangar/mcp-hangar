**core:** the OTLP audit log exporter always sent plaintext gRPC, even where
the trace exporter used TLS for the same endpoint, and it ignored the standard
exporter variables. It now resolves them as the trace exporter does, for the
logs signal. A scheme-less audit endpoint such as `collector:4317` therefore
now uses TLS; write `http://collector:4317`, or set
`OTEL_EXPORTER_OTLP_LOGS_INSECURE` or `OTEL_EXPORTER_OTLP_INSECURE` to `true`,
to keep plaintext. `https://` always uses TLS.
`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` now beats `OTEL_EXPORTER_OTLP_ENDPOINT`, and
`OTEL_EXPORTER_OTLP_LOGS_PROTOCOL` or `OTEL_EXPORTER_OTLP_PROTOCOL` selects
`grpc`, the default, or `http/protobuf`. Any other protocol, or a missing
exporter package, builds no audit log pipeline and logs the reason, and audit
records then go to the structured log. What turns audit export on is
unchanged: `OTEL_EXPORTER_OTLP_ENDPOINT` or `observability.tracing.otlp_endpoint`.
The audit endpoint is no longer written to the log

**core:** the trace exporter honours the standard OTLP exporter configuration
instead of overriding it. `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` or
`OTEL_EXPORTER_OTLP_PROTOCOL` selects the exporter: `grpc`, the default, or
`http/protobuf`. Any other value, or a missing exporter package, adds no OTLP
exporter and logs the reason. `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` now takes
effect and beats `OTEL_EXPORTER_OTLP_ENDPOINT`. `observability.tracing.otlp_endpoint`
in config.yaml is still used when neither is set, and the environment still
beats it, as before. `OTEL_EXPORTER_OTLP_INSECURE` and
`OTEL_EXPORTER_OTLP_TRACES_INSECURE` are honoured. A scheme-less gRPC endpoint
such as `collector:4317` therefore now uses TLS, as the OpenTelemetry SDK
defaults; write `http://collector:4317`, or set one of those to `true`, to keep
plaintext. `https://` always uses TLS, and the default `http://localhost:4317`
stays plaintext

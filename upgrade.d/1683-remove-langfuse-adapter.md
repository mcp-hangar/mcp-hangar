### The Langfuse adapter and its settings are removed: send spans to Langfuse over OTLP

Nothing called the Langfuse adapter after 2.22.0, so these settings sent
nothing to Langfuse. They are removed:

- `observability.langfuse` in `config.yaml` (`enabled`, `public_key`,
  `secret_key`, `host`, `sample_rate`, `scrub_inputs`, `scrub_outputs`)
- `MCP_LANGFUSE_ENABLED`, `MCP_LANGFUSE_SAMPLE_RATE`,
  `MCP_LANGFUSE_SCRUB_INPUTS`, `MCP_LANGFUSE_SCRUB_OUTPUTS`
- `HANGAR_LANGFUSE_ENABLED`, `HANGAR_LANGFUSE_SAMPLE_RATE`,
  `HANGAR_LANGFUSE_SCRUB_INPUTS`, `HANGAR_LANGFUSE_SCRUB_OUTPUTS`
- the `langfuse` extra (`pip install mcp-hangar[langfuse]`)
- in Python, `create_runtime(observability_config=...)`,
  `Runtime.observability`, `Runtime.observability_config`,
  `mcp_hangar.bootstrap.runtime.ObservabilityConfig`, the
  `mcp_hangar.integrations` package, `ApplicationContext.observability_adapter`;
  `init_observability()` now returns only the parsed config, and
  `shutdown_observability()` takes no argument

What happens to a configuration that still sets them:

- A scrub setting (`MCP_LANGFUSE_SCRUB_*`, `HANGAR_LANGFUSE_SCRUB_*`, or
  `observability.langfuse.scrub_*`), whatever its value, **refuses the boot**
  with a `ConfigurationError` naming it. Before, it was accepted and did
  nothing. Delete it. Hangar's spans carry no tool arguments or results; to
  redact what they do carry, put an OpenTelemetry Collector with an
  `attributes` or `redaction` processor in front of Langfuse.
- The other environment variables are named in a `langfuse_settings_removed`
  warning, and the boot continues.
- The rest of an `observability.langfuse` block is named as removed by the
  unknown-key check: a warning, or a refusal under `HANGAR_CONFIG_STRICT` and
  in `mcp-hangar config check`.

`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` are the
Langfuse SDK's own variables and are left alone.

To send Hangar's spans to Langfuse, use the OTLP exporter (Langfuse accepts
OTLP over HTTP, not gRPC):

```sh
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://cloud.langfuse.com/api/public/otel/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Basic%20<base64 of public_key:secret_key>,x-langfuse-ingestion-version=4"
```

Use the `TRACES_` variables: the generic `OTEL_EXPORTER_OTLP_ENDPOINT` also
turns on OTLP audit log export, which Langfuse does not accept. The full recipe
is `examples/langfuse/README.md`.

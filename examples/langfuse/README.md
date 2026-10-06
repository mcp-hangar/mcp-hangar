# MCP Hangar + Langfuse

Send Hangar's OpenTelemetry spans to Langfuse over OTLP.

## How it works

Langfuse accepts OpenTelemetry traces on an OTLP/HTTP endpoint. Hangar's trace
exporter is the standard OpenTelemetry one, so Langfuse is configured like any
other OTLP backend: no Langfuse package and no Langfuse-specific setting.

Hangar used to ship a Langfuse adapter (`observability.langfuse`,
`MCP_LANGFUSE_*`, `HANGAR_LANGFUSE_*`). Nothing called it after 2.22.0, and it
was removed (#1683). A config that still sets one of its scrub settings is
refused at startup; the other settings are named in a warning.

## Prerequisites

A running Langfuse instance (cloud or self-hosted, v3.22.0 or later) and a
project's public and secret keys, from <https://cloud.langfuse.com/> or your
self-hosted deployment. Install Hangar with the `opentelemetry` extra.

## Configuration

Langfuse takes OTLP over HTTP only, not gRPC, so set the protocol as well as
the endpoint. The credential is HTTP Basic auth over `public_key:secret_key`:

```sh
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
AUTH_STRING=$(printf '%s' "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" | base64 | tr -d '\n')

export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://cloud.langfuse.com/api/public/otel/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Basic%20${AUTH_STRING},x-langfuse-ingestion-version=4"
```

- Other regions: `https://us.cloud.langfuse.com/...`, `https://jp.cloud.langfuse.com/...`;
  self-hosted: `https://<your-host>/api/public/otel/v1/traces`.
- The `%20` is the space in `Basic <credential>`, percent-encoded as the
  OpenTelemetry header format requires.
- Use the `TRACES_` variables, not the generic `OTEL_EXPORTER_OTLP_ENDPOINT`:
  the generic one also turns on Hangar's OTLP audit log export, and Langfuse
  does not accept logs.

To send to Langfuse and another backend, or to redact span attributes before
they leave your network, run an OpenTelemetry Collector and give it Langfuse as
an `otlphttp` exporter with the same endpoint and header.

## What Langfuse receives

Hangar's spans, as any OTLP backend receives them: names, ids, outcomes and
durations, and the trace context propagated from the caller. Tool arguments
and results are not on spans; caller user, agent and session ids are only when
`observability.tracing.caller_ids` is on.

## Security note

`LANGFUSE_SECRET_KEY` is a secret, and so is the header built from it. Never
commit either to config files or source control. Use environment variables,
HashiCorp Vault, or Kubernetes secrets.

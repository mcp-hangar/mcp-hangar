# MCP Hangar + Langfuse

Configure Hangar to send LLM observability data to Langfuse.

## How it works

The `LangfuseObservabilityAdapter` in Hangar wraps tool invocations as Langfuse
traces and generations. It complements (not replaces) the OTEL trace path:

- OTEL spans: governance telemetry (enforcement, violations, state changes)
- Langfuse: LLM-specific observability (input/output, token counts, user sessions)

## Prerequisites

A running Langfuse instance (cloud or self-hosted). Get your keys from
<https://cloud.langfuse.com/> or your self-hosted deployment.

## Configuration

Set these environment variables before starting Hangar:

```sh
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com  # or your self-hosted URL
```

In `config.yaml`:

```yaml
observability:
  langfuse:
    enabled: true
    # Keys are read from environment variables above
    # Never put secret keys in config files
```

## What Hangar sends to Langfuse

| Langfuse concept | Hangar mapping |
|-----------------|----------------|
| Trace | One MCP session (session_id) |
| Span | Provider tool invocation |
| Generation | Tool call with input/output |
| User | user_id from identity propagation |

## Attribute alignment with OTEL conventions

When Hangar propagates caller identity (v0.15.0+), Langfuse traces carry the
same `user_id` and `session_id` as the MCP OTEL spans. This makes it possible
to correlate Langfuse traces with governance enforcement events in OTEL backends.

## Security note

`LANGFUSE_SECRET_KEY` is a secret. Never commit it to config files or
source control. Use environment variables, HashiCorp Vault, or k8s secrets.
The `${LANGFUSE_SECRET_KEY}` syntax in config files triggers env var interpolation.

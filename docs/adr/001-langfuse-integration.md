# ADR-001: Langfuse Integration for LLM Observability

**Status:** Accepted
**Date:** 2026-01-12
**Authors:** MCP Hangar Team

## Context

MCP Hangar manages tool invocations from LLM applications through MCP providers. Operators need end-to-end visibility into these invocations to:

1. Debug production issues (latency, errors, timeouts)
2. Track costs per provider/tool/user
3. Correlate provider degradation with LLM response quality
4. Run quality evaluations on tool outputs

Existing observability (Prometheus metrics, OpenTelemetry traces) provides infrastructure-level visibility but lacks LLM-specific context like trace correlation with prompts and cost attribution.

## Decision

We will integrate MCP Hangar with [Langfuse](https://langfuse.com), an open-source LLM observability platform, using the following architecture:

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Application Layer                          │
│  ┌───────────────────┐  ┌─────────────────────────────────┐ │
│  │ ObservabilityPort │◄─│ TracedProviderService           │ │
│  │     (interface)   │  │ (decorator for ProviderService) │ │
│  └───────────────────┘  └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Infrastructure Layer                       │
│  ┌─────────────────────────┐  ┌───────────────────────────┐ │
│  │ LangfuseObservability   │  │ NullObservabilityAdapter  │ │
│  │      Adapter            │  │  (when disabled)          │ │
│  └─────────────────────────┘  └───────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                       [ Langfuse SDK ]
```

### Key Design Decisions

1. **Port/Adapter Pattern**: `ObservabilityPort` is an abstract interface in the application layer. This allows:
   - Swapping observability backends without changing application code
   - Easy testing with `NullObservabilityAdapter`
   - Future support for other platforms (e.g., Weights & Biases, LangSmith)

2. **Decorator Pattern**: `TracedProviderService` wraps `ProviderService` rather than modifying it, preserving the original service and allowing optional tracing.

3. **Optional Dependency**: Langfuse SDK is an optional dependency (`pip install mcp-hangar[observability]`). When not installed, the system gracefully falls back to `NullObservabilityAdapter`.

4. **Thread-Safe Design**: All Langfuse operations are protected with `threading.Lock` to match MCP Hangar's thread-based (not async) architecture.

5. **Sampling Support**: Configurable `sample_rate` for high-traffic deployments to reduce observability costs.

6. **GDPR Compliance**: Optional `scrub_inputs` and `scrub_outputs` flags to redact sensitive data before sending to Langfuse.

### Trace Context Propagation

Traces can be correlated with external LLM applications by passing `trace_id`, `user_id`, and `session_id` when invoking tools:

```python
result = traced_service.invoke_tool(
    provider_id="math",
    tool_name="add",
    arguments={"a": 1, "b": 2},
    trace_id="langfuse-trace-from-llm-app",
)
```

## Consequences

### Positive

- **End-to-end visibility**: Operators can trace from LLM prompt to provider response
- **Cost attribution**: Clear breakdown of costs per provider/tool/user
- **Quality correlation**: Ability to correlate provider health with output quality
- **Non-invasive**: Existing code unchanged; tracing is opt-in
- **Testable**: `NullObservabilityAdapter` makes testing easy

### Negative

- **Additional dependency**: Langfuse SDK adds ~2MB to package size
- **Network overhead**: Each traced invocation sends data to Langfuse (mitigated by async flushing)
- **Langfuse lock-in**: While the port pattern allows swapping, trace data is in Langfuse format

### Neutral

- **Not auto-instrumented**: MCP protocol doesn't support standard trace headers (W3C Trace Context), so manual trace propagation is required
- **Two observability systems**: Langfuse for LLM context, OpenTelemetry for infrastructure (by design - different concerns)

## Alternatives Considered

### 1. OpenTelemetry Only
- **Rejected**: OpenTelemetry doesn't provide LLM-specific features (cost tracking, evals, prompt correlation)

### 2. Custom Solution
- **Rejected**: Building custom observability would duplicate Langfuse's features

### 3. LangSmith
- **Deferred**: Langfuse is open-source and can be self-hosted; LangSmith requires LangChain ecosystem

## References

- [Langfuse Documentation](https://langfuse.com/docs)
- [MCP Protocol Specification](https://modelcontextprotocol.io/specification)
- [ObservabilityPort Implementation](/mcp_hangar/application/ports/observability.py)
- [LangfuseAdapter Implementation](/mcp_hangar/infrastructure/observability/langfuse_adapter.py)

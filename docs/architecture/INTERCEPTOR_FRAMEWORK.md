# Interceptor Framework

MCP Hangar implements the SEP-1763 interceptor framework with hook-based event delivery and priority-ordered mutator pipelines. See [ADR-005](../adr/ADR-005-sep-1763-interceptor-compliance.md) for design rationale.

## Architecture

```
Tool invocation
    |
    v
DigestValidator            (ADR-004: schema integrity check)
    |  emits DigestMismatchEvent on mismatch
    v
MutatorPipeline            (ADR-005: sequential transformation)
    |  ResponseTruncator, future: PII redaction, schema enforcement
    v
EventBus.publish()
    |
    +---> flat subscribers     (backward-compatible)
    +---> hook subscribers     (phase-wrapped Hook objects)
    +---> wildcard filters     (EventPattern matching)
```

## Components

### Digest Pinning (ADR-004)

| Type | Location | Purpose |
|------|----------|---------|
| `ToolDigest` | `domain/value_objects/tool_digest.py` | SHA-256 fingerprint of a tool's canonical schema |
| `DigestPolicy` | `domain/value_objects/tool_digest.py` | Enforcement level + unknown-tool handling + allowlist |
| `DigestEnforcement` | `domain/value_objects/tool_digest.py` | Enum: `audit`, `warn`, `block` |
| `compute_tool_digest()` | `domain/services/digest_computation.py` | Deterministic SHA-256 over canonical JSON |
| `DigestValidator` | `domain/services/digest_validator.py` | Validates tools against policy, emits `DigestMismatchEvent` |

### Hook-Based Event Model (ADR-005)

| Type | Location | Purpose |
|------|----------|---------|
| `HookPhase` | `domain/value_objects/hook.py` | Enum: `BEFORE`, `AROUND`, `AFTER`, `ON_ERROR`, `OBSERVE` |
| `Hook` | `domain/value_objects/hook.py` | Wraps `(event, phase, sequence_number)` |
| `IHookSubscriber` | `domain/contracts/hook_subscriber.py` | Protocol for phase-aware event delivery |
| `EventBus` | `infrastructure/event_bus.py` | Fan-out to both flat subscribers and hook subscribers |

### Mutator Pipeline (ADR-005)

| Type | Location | Purpose |
|------|----------|---------|
| `IMutator` | `domain/contracts/mutator.py` | Protocol: `priority_hint`, `applies_to`, `mutate()` |
| `MutationContext` | `domain/contracts/mutator.py` | Input: method, direction, payload, correlation_id |
| `MutationResult` | `domain/contracts/mutator.py` | Output: payload, changed flag, audit_only flag |
| `MutatorPipeline` | `application/services/mutator_pipeline.py` | Sorts by `(priority_hint, registration_index)`, executes sequentially |
| `ResponseTruncator` | `application/mutators/response_truncator.py` | Truncates oversized `tools/call` responses, emits `ResponseTruncated` |

### Wildcard Subscriptions (ADR-005)

| Type | Location | Purpose |
|------|----------|---------|
| `EventPattern` | `domain/value_objects/event_pattern.py` | Segment-wise wildcard matching (`*`, `tools/*`, `*/response`) |
| `compile_event_patterns()` | `server/api/ws/filters.py` | Compiles raw strings into `EventPattern` objects |
| `matches_filters()` | `server/api/ws/filters.py` | Tests events against wildcard-aware subscription filters |

### Interceptor Discoverability

`GET /interceptors/list` returns mcp-hangar's capabilities as a SEP-1763 interceptor:

```json
{
  "interceptors": [
    {
      "name": "mcp-hangar",
      "version": "<package version>",
      "types": ["validator", "mutator", "observer"],
      "capabilities": {
        "failOpen": true,
        "auditMode": true,
        "trustBoundaryAware": true
      }
    }
  ]
}
```

## Mutator Ordering

Mutators execute in ascending `priority_hint` order. Ties are broken by registration order (stable sort).

| Mutator | priority_hint | Rationale |
|---------|--------------|-----------|
| (future: PII redactor) | 100 | Runs early to redact before other transforms |
| (future: schema enforcer) | 500 | Validates structure after redaction |
| ResponseTruncator | 1000 | Runs last to truncate after all other transforms |

## Event Flow

1. `DigestValidator.validate_tool()` produces `DigestValidationResult` with optional `DigestMismatchEvent`.
2. Caller publishes events via `EventBus.publish()`.
3. EventBus delivers to flat subscribers (type-matched), hook subscribers (phase-wrapped), and wildcard-filtered WebSocket streams.
4. `MutatorPipeline.execute()` runs registered mutators sequentially.
5. Mutators collect domain events (e.g., `ResponseTruncated`) via `event_collector` list pattern.
6. Caller publishes mutator events to EventBus for audit trail.

## P2 Items (Not Yet Implemented)

- `interceptor/invoke` JSON-RPC method (explicit invocation mode)
- Shadow mutations (audit mode on mutators)
- Per-interceptor `failOpen` granularity
- Extended lifecycle events (`resources/*`, `prompts/*`, `sampling/*`, `elicitation/*`, `roots/*`)

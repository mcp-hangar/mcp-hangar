# ADR-002: Event Sourcing

**Status:** Accepted
**Date:** 2026-04-17
**Authors:** MCP Hangar Team

## Context

MCP Hangar requires a robust audit trail of all state changes to providers for compliance and debugging. Traditional state-based persistence (saving only the current snapshot) loses the history of how the system reached its current state. Additionally, rebuilding complex aggregates like `Provider` with intricate state machine transitions and circuit breaker logic is difficult with standard ORM mapping.

## Decision

We have adopted Event Sourcing as the primary persistence mechanism for domain aggregates.

### Implementation Details

1. **Domain Events**: Aggregates collect domain events (`mcp_hangar.domain.events.DomainEvent`) during state transitions.
2. **EventStore**: Events are persisted in an append-only log (`mcp_hangar.infrastructure.event_store.EventStore`). We support both `InMemoryEventStore` and `FileEventStore`.
3. **Aggregate Rehydration**: Aggregates are rebuilt by loading their entire event stream from the `EventStore` and applying events sequentially (`mcp_hangar.domain.model.event_sourced_provider.py`).
4. **Optimistic Concurrency**: The `EventStore` uses version numbers and expected versions during `append` to detect and prevent concurrent modification conflicts (`ConcurrencyError`).
5. **Snapshots**: To optimize performance for aggregates with long histories, the system takes snapshots every 50 events (`EventStoreSnapshot`). Loading an aggregate starts from the latest snapshot and replays only subsequent events.
6. **Upcasting**: Changes to the structure of domain events over time are handled via event upcasting in the infrastructure layer, ensuring that old events can still be loaded into newer domain models.

## Consequences

### Positive

- **Complete Audit Trail**: Every state change is captured as a discrete event, providing perfect visibility for compliance (e.g., SOC2, EU AI Act).
- **Time Travel**: The system can reconstruct the state of any aggregate at any point in history for debugging or reporting.
- **Simplified Persistence**: The infrastructure layer only needs to store immutable event objects, avoiding complex relational mapping for deeply nested or logic-heavy domain models.
- **Improved Performance**: Writes are high-performance append-only operations, and snapshots mitigate the overhead of replaying large event streams.

### Negative

- **Learning Curve**: Event sourcing is a mental shift from traditional state-based persistence and requires careful management of event schemas.
- **Event Versioning**: Schema evolution must be handled explicitly through upcasting or versioned event classes.
- **Storage Volume**: The event store grows indefinitely. While snapshots keep load times constant, storage costs and archival strategies must be managed over time.
- **Eventual Consistency**: Read models must be updated based on events, leading to temporary inconsistencies between the write-side and read-side.

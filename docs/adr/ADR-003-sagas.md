# ADR-003: Saga Pattern

**Status:** Accepted
**Date:** 2026-04-17
**Authors:** MCP Hangar Team

## Context

MCP Hangar needs to manage complex, long-running processes that span multiple domain aggregates or even external services (e.g., K8s resources). These operations, such as provider failover or recovery, cannot be handled as a single atomic transaction. Traditional distributed transactions (2PC) are not scalable and add significant complexity to a system where individual components may fail or restart independently.

## Decision

We have implemented the Saga pattern to manage distributed, multi-step business processes with compensating actions for failure handling.

### Saga Types

We support three primary saga types:
1. **Provider Failover Saga** (`mcp_hangar.application.sagas.provider_failover_saga.ProviderFailoverSaga`): Orchestrates primary-to-backup provider transitions in three steps (start backup, await primary, failback).
2. **Provider Recovery Saga** (`mcp_hangar.application.sagas.provider_recovery_saga.ProviderRecoverySaga`): Automatically restarts degraded providers with backoff.
3. **Group Rebalance Saga** (`mcp_hangar.application.sagas.group_rebalance_saga.GroupRebalanceSaga`): Manages load balancing and re-distribution of providers within a group.

### Implementation Details

- **SagaManager**: Orchestrates saga execution and persists state (`mcp_hangar.infrastructure.saga_manager.SagaManager`).
- **Step-based Sagas**: Sagas define a series of named steps. Each step includes a command and a corresponding compensation command to roll back changes if a subsequent step fails.
- **Event-triggered Sagas**: `EventTriggeredSaga` instances (`mcp_hangar.application.ports.saga.EventTriggeredSaga`) listen for domain events and initiate step-based sagas or other commands.
- **Compensation**: If a step in a saga fails, the `SagaManager` executes the compensation commands for all previously successful steps in reverse order, ensuring the system returns to a consistent state.
- **Scheduled Commands**: Sagas can schedule future commands with specific delays, which is used for auto-failback logic (`sm.schedule_command`).

## Consequences

### Positive

- **Fault Tolerance**: Sagas provide a robust framework for handling failures in long-running processes through automated compensations.
- **Event-driven Orchestration**: Sagas naturally integrate with our domain event system, allowing complex workflows to react to system state changes.
- **Visibility**: Saga state is persisted and observable, providing a clear view of the current progress of long-running operations.
- **Maintainability**: Complex logic is encapsulated into discrete, testable steps rather than being scattered across multiple event handlers.

### Negative

- **Eventual Consistency**: Sagas do not provide ACID guarantees; the system may be in an intermediate state while a saga is in progress.
- **Design Complexity**: Defining effective compensation logic can be challenging, especially for external side effects that are difficult to undo.
- **State Management**: Saga state must be carefully managed and persisted across application restarts to ensure that long-running processes can resume correctly.

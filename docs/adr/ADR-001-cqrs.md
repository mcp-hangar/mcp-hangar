# ADR-001: Command Query Responsibility Segregation (CQRS)

**Status:** Accepted
**Date:** 2026-04-17
**Authors:** MCP Hangar Team

## Context

MCP Hangar manages complex provider lifecycles and tool invocations while serving high-volume read requests for provider status and metrics. A traditional CRUD-based architecture where the same models are used for both reading and writing leads to several problems:

1. **Model Overload**: Aggregate roots like `Provider` become bloated with read-only logic and DTO transformations.
2. **Performance Constraints**: Complex queries often require joins or data transformations that are inefficient when executed against normalized domain models.
3. **Scalability**: Scaling write operations (state transitions, circuit breaking) independently from read operations (dashboard views, tool listing) is difficult.

## Decision

We have implemented the Command Query Responsibility Segregation (CQRS) pattern to separate the write-side (intent to change state) from the read-side (requests for data).

### Write-side: Commands

Commands represent a user's intent to change the system state.
- **CommandBus**: The central dispatcher (`mcp_hangar.infrastructure.command_bus.CommandBus`) routes commands to exactly one handler.
- **Handlers**: Command handlers (`mcp_hangar.application.commands.handlers.py`) encapsulate the orchestration of domain logic, typically interacting with an aggregate root.
- **Middleware**: The `CommandBus` supports a middleware pipeline (`CommandBusMiddleware`) for cross-cutting concerns like tracing and rate limiting (`RateLimitMiddleware`).

### Read-side: Queries

Queries are read-only requests for data with no side effects.
- **QueryBus**: Dispatches queries to their respective handlers (`mcp_hangar.infrastructure.query_bus.QueryBus`).
- **Read Models**: Queries return denormalized projections or DTOs rather than domain aggregates, optimized for specific UI or API needs.
- **Isolation**: Read logic is completely separate from domain logic, ensuring that expensive queries cannot interfere with the integrity of state transitions.

### Implementation Details

- **Command Classes**: Defined in `mcp_hangar.application.commands.commands.py`.
- **Query Classes**: Defined in `mcp_hangar.application.queries.queries.py`.
- **Bus Registration**: Handlers are registered in the composition root (`mcp_hangar.server.bootstrap.cqrs.py`).

## Consequences

### Positive

- **Separation of Concerns**: Domain aggregates focus on state transitions and business rules; read models focus on presentation.
- **Optimized Reads**: Query handlers can use optimized SQL or denormalized stores without impacting the domain layer.
- **Extensibility**: Adding new UI views or API endpoints often only requires adding a new Query and Handler without touching the core domain.
- **Improved Security**: Command middleware provides a central place to enforce global security and rate-limiting policies.

### Negative

- **Increased Complexity**: Developers must manage twice as many classes (Commands/Handlers and Queries/Handlers).
- **Boilerplate**: Simple CRUD operations require more ceremony compared to traditional ORM-based approaches.
- **Eventual Consistency**: While not currently enforced by a separate database, the pattern naturally leads towards a design where read models are updated asynchronously via domain events.

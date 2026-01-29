# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-01-29

### Added

- **Batch Invocations**: New `hangar_batch()` tool for parallel tool execution
  - Execute multiple tool invocations in a single API call
  - Configurable concurrency (1-20 parallel workers)
  - Single-flight pattern for cold starts (one provider starts once, not N times)
  - Partial success handling (continue on error by default)
  - Fail-fast mode (abort on first error)
  - Per-call and global timeout support
  - Circuit breaker integration (CB OPEN = instant error)
  - Response truncation for oversized payloads (10MB per call, 50MB total)
  - Eager validation before execution
  - Full observability (batch_id, call_id, Prometheus metrics)

- **SingleFlight Pattern**: New `SingleFlight` class in `infrastructure/single_flight.py`
  - Ensures a function executes only once for a given key
  - Thread-safe implementation with result caching option
  - Used for cold start deduplication in batch operations

- **Domain Events**: New batch-related domain events
  - `BatchInvocationRequested` - When batch starts
  - `BatchInvocationCompleted` - When batch finishes
  - `BatchCallCompleted` - Per-call completion

- **Prometheus Metrics**: New batch metrics
  - `mcp_hangar_batch_calls_total{result}` - Total batch invocations
  - `mcp_hangar_batch_size_histogram` - Calls per batch distribution
  - `mcp_hangar_batch_duration_seconds` - Batch execution time
  - `mcp_hangar_batch_concurrency_gauge` - Current parallel executions
  - `mcp_hangar_batch_truncations_total{reason}` - Response truncations
  - `mcp_hangar_batch_circuit_breaker_rejections_total{provider}` - CB rejections
  - `mcp_hangar_batch_cancellations_total{reason}` - Batch cancellations

### Documentation

- New guide: `docs/guides/BATCH_INVOCATIONS.md`

## [0.4.0] - 2026-01-29

### Changed

**BREAKING: Full rebrand from "registry" to "hangar" terminology.**

MCP Hangar is a **control plane**, not a registry. The [MCP Registry](https://registry.modelcontextprotocol.io) is the official catalog for discovering MCP servers. MCP Hangar manages runtime lifecycle. This rename eliminates confusion between the two projects.

#### MCP Tool Renames

All MCP tools renamed from `registry_*` to `hangar_*`:

| Old | New |
|-----|-----|
| `registry_list` | `hangar_list` |
| `registry_start` | `hangar_start` |
| `registry_stop` | `hangar_stop` |
| `registry_invoke` | `hangar_invoke` |
| `registry_tools` | `hangar_tools` |
| `registry_details` | `hangar_details` |
| `registry_health` | `hangar_health` |
| `registry_discover` | `hangar_discover` |
| `registry_discovered` | `hangar_discovered` |
| `registry_quarantine` | `hangar_quarantine` |
| `registry_approve` | `hangar_approve` |
| `registry_sources` | `hangar_sources` |
| `registry_metrics` | `hangar_metrics` |
| `registry_group_list` | `hangar_group_list` |
| `registry_group_rebalance` | `hangar_group_rebalance` |

#### Python API Renames

Protocol classes and dataclass renamed in `fastmcp_server.py`:

| Old | New |
|-----|-----|
| `RegistryFunctions` | `HangarFunctions` |
| `RegistryListFn` | `HangarListFn` |
| `RegistryStartFn` | `HangarStartFn` |
| `RegistryStopFn` | `HangarStopFn` |
| `RegistryInvokeFn` | `HangarInvokeFn` |
| `RegistryToolsFn` | `HangarToolsFn` |
| `RegistryDetailsFn` | `HangarDetailsFn` |
| `RegistryHealthFn` | `HangarHealthFn` |
| `RegistryDiscoverFn` | `HangarDiscoverFn` |
| `RegistryDiscoveredFn` | `HangarDiscoveredFn` |
| `RegistryQuarantineFn` | `HangarQuarantineFn` |
| `RegistryApproveFn` | `HangarApproveFn` |
| `RegistrySourcesFn` | `HangarSourcesFn` |
| `RegistryMetricsFn` | `HangarMetricsFn` |

Builder method renamed: `with_registry()` -> `with_hangar()`
Property renamed: `factory.registry` -> `factory.hangar`

#### Prometheus Metric Renames

All metrics renamed from `mcp_registry_*` to `mcp_hangar_*`:

| Old | New |
|-----|-----|
| `mcp_registry_tool_calls_total` | `mcp_hangar_tool_calls_total` |
| `mcp_registry_tool_call_duration_seconds` | `mcp_hangar_tool_call_duration_seconds` |
| `mcp_registry_provider_state` | `mcp_hangar_provider_state` |
| `mcp_registry_cold_starts_total` | `mcp_hangar_cold_starts_total` |
| `mcp_registry_health_checks` | `mcp_hangar_health_checks` |
| `mcp_registry_circuit_breaker_state` | `mcp_hangar_circuit_breaker_state` |

**Action required:** Update Grafana dashboards and Prometheus alert rules.

### Removed

- **Backward compatibility layer removed** - no more deprecated aliases:
  - `RegistryFunctions` (use `HangarFunctions`)
  - `registry_list` (use `hangar_list`)
  - `with_registry()` (use `with_hangar()`)
  - `setup_fastmcp_server()` (use `MCPServerFactory`)
  - `create_fastmcp_server()` (use `MCPServerFactory.create_server()`)
  - `run_fastmcp_server()` (use `MCPServerFactory.create_asgi_app()`)

### Fixed

- Removed emoji from status indicators (per coding guidelines)

### Documentation

- Updated all documentation to use "control plane" terminology
- Updated SYSTEM_PROMPT.md with new tool names
- Updated Grafana dashboards with new metric names
- Updated copilot-instructions.md with new metric names

## [0.3.1] - 2026-01-24

### Added

- **Core**: Enhanced `ProviderStartError` with diagnostic information
  - `stderr`: Captured process stderr output
  - `exit_code`: Process exit code for failed starts
  - `suggestion`: Actionable suggestions based on error patterns
  - `get_user_message()`: Human-readable error message method
- **Core**: Automatic error pattern detection with suggestions for common issues:
  - Python errors (ModuleNotFoundError, ImportError, SyntaxError)
  - Permission and file errors
  - Network/connection errors
  - Docker/Podman container issues
  - Memory/resource errors
  - Common exit codes (1, 2, 126, 127, 137, 139)

### Documentation

- Updated troubleshooting guide with provider startup error diagnostics
- Added programmatic error handling examples

## [0.3.0] - 2026-01-21

### Added

- **Facade API**: New high-level `Hangar` class for simplified provider management
  - Async-first API with `await hangar.invoke()`, `await hangar.health()`
  - Sync wrapper `SyncHangar` for simple scripting use cases
  - Context manager support: `async with Hangar.from_config(...) as hangar:`
- **HangarConfig Builder**: Programmatic configuration with fluent API
  - `.add_provider()` for subprocess, docker, and remote providers
  - `.enable_discovery()` for Docker/Kubernetes/filesystem auto-discovery
  - Type-safe validation at build time
- **Quick Install Script**: `curl -sSL https://get.mcp-hangar.io | bash`

### Improved

- **Infrastructure**: Thread-safe lock hierarchy with `HierarchicalLockManager`
  - Deadlock prevention via strict acquisition ordering
  - Lock timeout support with configurable defaults
  - Context manager API for safe lock management
- **Test Coverage**: +77 new unit tests
  - Facade tests (49 tests)
  - Knowledge base memory backend tests (28 tests)
  - Auth middleware tests (30 tests)
- **Documentation**: All links updated to `mcp-hangar.io`

### Changed

- **Breaking**: `bootstrap()` now accepts optional `config_dict` parameter for programmatic config
- **Dependencies**: Updated minimum Python version requirement clarified as 3.11+

## [0.2.3] - 2026-01-20

### Fixed

- **Core**: Improved error diagnostics for provider startup failures - stderr from container/subprocess is now included in error messages instead of generic "unknown error"
- **Core**: `StdioClient` now captures and propagates stderr to error messages when process dies
- **Core**: `Provider._handle_start_failure()` now receives actual exception instead of None

## [0.2.2] - 2026-01-19

### Fixed

- **CI**: Re-enable mypy type checking in CI with gradual adoption configuration
- **Core**: Configure mypy with relaxed settings for gradual type safety improvement
- **Core**: Disable specific mypy error codes during transition period (union-attr, arg-type, override, etc.)

### Technical Debt Notes

The following items are documented technical debt introduced to enable CI:
- **Mypy not in strict mode**: Currently using relaxed settings with many error codes disabled. Plan to gradually enable stricter checking. See `pyproject.toml` for full list of disabled error codes.
- **Docker push disabled**: Requires organization package write permissions in GitHub settings.

## [0.2.1] - 2026-01-18

### Fixed

- **Core**: Add missing `ToolSchema` export in `models.py` for backward compatibility
- **Core**: Fix Python lint errors (E501 line too long, F401 unused imports)
- **Core**: Configure ruff ignore rules for stylistic warnings
- **Core**: Fix `# type:` comment interpreted as type annotation by mypy
- **CI**: Update Go version to 1.23 consistently across Dockerfile and workflows
- **CI**: Fix golangci-lint errcheck warnings in operator tests
- **CI**: Use dynamic repository names instead of hardcoded organization
- **CI**: Temporarily disable mypy (requires strict mode refactoring)
- **CI**: Temporarily disable docker push jobs (requires org package permissions)

## [0.2.0] - 2026-01-18

### Added

#### Authentication & Authorization (TASK-001)
- **API Key Authentication**: Secure API key-based authentication
  - API key generation with `mcp_` prefix for easy identification
  - Key hashing with SHA-256 for secure storage
  - Key expiration and revocation support
  - In-memory and PostgreSQL key stores

- **JWT/OIDC Authentication**: Enterprise SSO integration
  - JWKS-based token validation
  - OIDC discovery support
  - Configurable claim mappings (subject, groups, tenant)
  - Tested with Keycloak integration

- **Role-Based Access Control (RBAC)**: Granular permissions
  - Built-in roles: admin, provider-admin, developer, viewer, auditor
  - Permission-based authorization (provider:*, tool:invoke, etc.)
  - Group-based role assignment
  - Tenant/scope isolation support

- **Event-Sourced Auth Storage**: Full audit trail
  - API key lifecycle events (created, used, revoked)
  - Role assignment events
  - PostgreSQL persistence with CQRS pattern

- **CLI Commands**: Key management
  - `mcp-hangar auth create-key` - Create API keys
  - `mcp-hangar auth list-keys` - List keys for principal
  - `mcp-hangar auth revoke-key` - Revoke API key
  - `mcp-hangar auth assign-role` - Assign roles

#### Kubernetes Operator (TASK-002)
- **MCPProvider CRD**: Declarative provider management
  - Container and remote provider modes
  - Configurable health checks and circuit breaker
  - Resource limits and security contexts
  - Environment variables from Secrets/ConfigMaps
  - Volume mounts (Secret, ConfigMap, PVC)

- **MCPProviderGroup CRD**: High availability
  - Label selector-based provider grouping
  - Load balancing strategies (RoundRobin, LeastConnections, Random, Failover)
  - Configurable failover with retries
  - Health policy enforcement

- **MCPDiscoverySource CRD**: Auto-discovery
  - Namespace-based discovery
  - ConfigMap-based discovery
  - Additive and Authoritative modes
  - Provider templates for defaults

- **Operator Features**:
  - State machine reconciliation (Cold → Initializing → Ready → Degraded → Dead)
  - Prometheus metrics for monitoring
  - Leader election for HA
  - Helm chart for deployment

### Changed
- **Domain**: Changed API group from `mcp.hangar.io` to `mcp-hangar.io` for consistency
- **Config**: Volume paths changed from absolute to relative in examples
- **Documentation**: Added comprehensive Kubernetes and Authentication guides

### Security
- All auth features are opt-in (disabled by default)
- Secure defaults for pod security contexts
- No hardcoded credentials in production code
- Testcontainers-based security testing

### Documentation
- New guide: `docs/guides/KUBERNETES.md` - Complete K8s integration guide
- New guide: `docs/guides/AUTHENTICATION.md` - Auth configuration guide
- Security audit: `docs/security/AUTH_SECURITY_AUDIT.md`
- Updated mkdocs navigation

## [0.1.4] - 2026-01-16

### Added
- **Event Store Implementation**: Full Event Sourcing support with persistence
  - `IEventStore` interface with SQLite and In-Memory implementations
  - Optimistic concurrency control for concurrent event appends
  - Event serialization/deserialization with JSON support
  - Integration with EventBus for automatic event persistence
  - `publish_to_stream()` and `publish_aggregate_events()` methods
  - Configurable via `event_store` section in config.yaml
  - Complete test coverage (33 new tests)

## [0.1.3] - 2026-01-14

### Skipped

## [0.1.2] - 2026-01-13

### Added
- **Langfuse Integration**: Optional LLM observability with Langfuse
  - Full trace lifecycle management (start, end, error handling)
  - Span nesting for tool invocations and provider operations
  - Automatic score recording for health checks and success rates
  - Graceful degradation when Langfuse is unavailable
  - Configuration via environment variables or config file

- **Testcontainers Support**: Production-grade integration testing
  - PostgreSQL, Redis, Prometheus, Langfuse container fixtures
  - Custom MCP provider container fixtures
  - Conditional loading - tests work without testcontainers installed

### Changed
- **Monitoring Stack Simplified**: Cleaner configuration structure
  - Combined critical/warning alerts into single `alerts.yaml`
  - Added Grafana datasource provisioning
  - Removed obsolete `version` attribute from docker-compose

### Fixed
- Fixed testcontainers import error in CI when library not installed
- Fixed Prometheus metrics `info` type (changed to `gauge` for compatibility)
- Fixed import sorting across all modules (ruff isort)
- Fixed documentation links to point to GitHub Pages
- Removed unused imports and variables

## [0.1.1] - 2026-01-12

### Added
- **Observability Module**: Comprehensive monitoring and tracing support
  - OpenTelemetry distributed tracing with OTLP/Jaeger export
  - Extended Prometheus metrics (circuit breaker, retry, queue depth, SLIs)
  - Kubernetes-compatible health endpoints (`/health/live`, `/health/ready`, `/health/startup`)
  - Pre-built Grafana dashboard for overview metrics
  - Prometheus alert rules (critical and warning)
  - Alertmanager configuration template
  - Documentation at `docs/guides/OBSERVABILITY.md`

- **Provider Groups**: Load balancing and high availability for multiple providers
  - Group multiple providers of the same type into a single logical unit
  - Five load balancing strategies: `round_robin`, `weighted_round_robin`, `least_connections`, `random`, `priority`
  - Automatic member health tracking with configurable thresholds
  - Group-level circuit breaker for cascading failure protection
  - Automatic retry on failure with different member selection
  - New tools: `registry_group_list`, `registry_group_rebalance`
  - Transparent API - existing tools work seamlessly with groups
  - Domain events for group lifecycle: `GroupCreated`, `GroupMemberAdded`, `GroupStateChanged`, etc.
  - Comprehensive documentation in `docs/PROVIDER_GROUPS.md`

## [0.1.0] - 2025-12-16

### Added
- Initial open source release
- Hot-loading MCP provider management with automatic lifecycle control
- Multiple transport modes: Stdio (default) and HTTP with Streamable HTTP support
- Container support for Docker and Podman with auto-detection
- Pre-built image support for running any Docker/Podman image directly
- Thread-safe operations with proper locking mechanisms
- Health monitoring with active health checks and circuit breaker pattern
- Automatic garbage collection for idle provider shutdown
- Provider state machine: `COLD → INITIALIZING → READY → DEGRADED → DEAD`
- Registry MCP tools: `registry_list`, `registry_start`, `registry_stop`, `registry_invoke`, `registry_tools`, `registry_details`, `registry_health`
- Comprehensive security features:
  - Input validation at API boundaries
  - Command injection prevention
  - Rate limiting with token bucket algorithm
  - Secrets management with automatic masking
  - Security audit logging
- Domain-Driven Design architecture with CQRS pattern
- Event sourcing support for provider state management
- Subprocess mode for local MCP server processes
- Container mode with security hardening (dropped capabilities, read-only filesystem, no-new-privileges)
- Volume mount support with blocked sensitive paths
- Resource limits (memory, CPU) for container providers
- Network isolation options (none, bridge, host)
- Example math provider for testing
- Comprehensive test suite (unit, integration, feature, performance tests)
- GitHub Actions CI/CD for linting and testing (Python 3.11-3.14)
- Pre-commit hooks for code quality (black, isort, ruff)
- Docker and docker-compose support for containerized deployment
- Extensive documentation:
  - API reference
  - Architecture overview
  - Security guide
  - Contributing guide
  - Docker support guide

### Security
- Input validation for all provider IDs, tool names, and arguments
- Command sanitization to prevent shell injection attacks
- Environment variable filtering to remove sensitive data
- Rate limiting to prevent denial of service
- Audit logging for security-relevant events

[Unreleased]: https://github.com/mapyr/mcp-hangar/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/mapyr/mcp-hangar/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/mapyr/mcp-hangar/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/mapyr/mcp-hangar/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/mapyr/mcp-hangar/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/mapyr/mcp-hangar/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/mapyr/mcp-hangar/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/mapyr/mcp-hangar/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/mapyr/mcp-hangar/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mapyr/mcp-hangar/releases/tag/v0.1.0

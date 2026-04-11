# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-04-11

First stable release. All public APIs are now covered by semantic versioning guarantees.

### Added

- **Enterprise Module System** (Phases 47, BSL 1.1):
  - `LicenseTier` enum (COMMUNITY, PRO, ENTERPRISE) with `LicenseValidator` HMAC-SHA256 key validation
  - `EnterpriseComponents` dataclass and `load_enterprise_modules()` bootstrap integration
  - License tier gating: enterprise features activate based on license key; all failure modes fall back to COMMUNITY
  - HMAC signing secret configurable via `HANGAR_LICENSE_HMAC_SECRET` environment variable (no longer hardcoded)

- **Capability Declaration and Enforcement** (Phases 38-41):
  - `ProviderCapabilities` value object with network, filesystem, environment, tool, and resource declarations
  - `from_dict()` factory and config.yaml integration for capability blocks
  - Kubernetes CRD types for capabilities with reconciler propagation to status
  - `NetworkPolicyBuilder` pure function generating Kubernetes NetworkPolicy from declared egress rules
  - Docker capabilities-aware network mode in `DockerLauncher`
  - `ViolationType` and `ViolationSeverity` enums with Prometheus violations counter
  - `ViolationRecord` CRD type and `ViolationDetected` condition in operator reconciler
  - CEL admission validation and `ExpectedTools` field in MCPProvider CRD
  - Wildcard egress override audit warning event

- **Behavioral Profiling** (Phases 42-44):
  - `IBehavioralProfiler`, `IBaselineStore`, `IDeviationDetector` contracts with null implementations
  - `BehavioralMode` enum, `NetworkObservation` value object, `BehavioralModeChanged` event
  - SQLite-backed `BaselineStore` for behavioral profiling data
  - `BehavioralProfiler` facade with enterprise bootstrap conditional loading
  - `DeviationDetector` with 3 detection rules (new destination, protocol drift, frequency anomaly)
  - ENFORCING mode support with event handler integration

- **Network Connection Monitoring** (Phase 43):
  - `/proc/net/tcp` and `ss` output parsers for connection tracking
  - `DockerNetworkMonitor` with container label injection
  - `K8sNetworkMonitor` with audit events and pod exec fallback
  - `ConnectionLogWorker` with monitor orchestration, bootstrap wiring, and config parsing

- **Tool Schema Drift Detection** (Phase 45):
  - MIT domain types for tool schema change tracking
  - `SchemaTracker` BSL class with SQLite storage and bootstrap wiring
  - `ToolSchemaChangeHandler` with event-driven schema diff detection

- **Resource Monitoring** (Phase 46):
  - `ResourceStore` with CRUD, baseline tracking, and pruning
  - `ResourceMonitorWorker` with bootstrap wiring and config integration
  - `BehavioralReportGenerator` with JSON and PDF export (via fpdf2)
  - Behavioral report REST endpoint with enterprise 403 gating

- **OpenTelemetry Governance Telemetry** (Phases 31-34):
  - `set_governance_attributes()` helper with MCP semantic convention constants
  - OTEL span integration in `TracedProviderService.invoke_tool`
  - W3C trace context extraction in `BatchExecutor` and injection in `HttpClient`
  - `OTLPAuditExporter` for security-relevant domain events with bootstrap wiring
  - OpenLIT integration recipe and OTEL Collector reference deployment example

- **Authorization Contracts** (Phase 35):
  - `IToolAccessPolicyEnforcer` protocol with `PolicyEvaluationResult`
  - `IDurableEventStore` ABC for persistent event storage
  - `NullAuthenticator`, `NullApiKeyStore` implementations for COMMUNITY tier
  - BSL 1.1 docstrings on all enterprise placeholder modules

- **Cloud Connector** (uplink to hangar-cloud SaaS):
  - Event payload redaction: tool arguments, error messages, and identity context stripped before cloud transmission
  - Bounded retry with dormant mode: registration stops after `max_registration_attempts`, then probes periodically
  - `CloudConfig` extended with `max_registration_attempts` and `dormant_probe_interval_s`

- **Approval Gate** (human-in-the-loop):
  - `mcp_tool_wrapper` decorator with optional `check_approval` async callback
  - Approval result with `approved`, `error_code`, `approval_id`, `reason` fields

- **Project Structure**:
  - Migrated from `packages/core/` to standard `src/mcp_hangar/` layout
  - Enterprise features separated into `enterprise/` directory under BSL 1.1
  - Enterprise import boundary enforced by `scripts/check_enterprise_boundary.sh`

### Changed

- **Development Status**: Promoted from Beta to Production/Stable
- **HMAC secret**: License key signing secret now read from `HANGAR_LICENSE_HMAC_SECRET` environment variable with dev-only fallback
- **Documentation URLs**: Consolidated to `mcp-hangar.io` (removed stale `github.io` references)

### Fixed

- Cloud connector: tool arguments no longer leak to cloud telemetry endpoint
- Cloud connector: infinite retry loop on failed registration replaced with bounded retry + dormant mode
- Docker Compose quickstart example: removed deprecated `version` key

## [0.12.0] - 2026-03-23

### Added

- **REST API Foundation** (Phases 11-12):
  - Full REST API at `/api/` prefix with CORS middleware, JSON serializers, and error handling
  - Provider endpoints: list, detail, start, stop, tool invocation history
  - Group and discovery source management endpoints
  - Config and system status endpoints
  - Auth endpoints with API key and role management
  - Observability endpoints (audit log, alerts)
  - WebSocket infrastructure: `ws_events_endpoint`, `ws_state_endpoint`, connection manager with queue and filters
  - `EventBus.unsubscribe_from_all` for WebSocket lifecycle

- **Provider Log Streaming** (Phases 21-22):
  - `LogLine` value object, `IProviderLogBuffer` contract, and `ProviderLogBuffer` ring buffer
  - Live stderr-reader threads for subprocess and Docker providers
  - `GET /api/providers/{id}/logs` REST endpoint with `lines` parameter
  - `LogStreamBroadcaster` and `/ws/providers/{id}/logs` WebSocket endpoint

- **Provider/Group CRUD** (Phase 23):
  - Provider CRUD events, commands, and handlers (create, update, delete)
  - Group CRUD handlers with `ProviderGroup.update()` and `to_config_dict()`
  - Config serializer module for export/backup
  - Provider and group CRUD REST endpoints
  - Config export and backup endpoints
  - Integration tests for CRUD operations and config serializer

- **RBAC and Tool Access Policies** (Phase 27):
  - Domain exceptions, events, and extended authorization contracts
  - `IRoleStore` extensions and `SQLiteToolAccessPolicyStore`
  - CQRS commands and query handlers for RBAC and TAP management
  - 10 REST route handlers for role and policy management
  - `tap_store` and `event_bus` wired through bootstrap and context

- **Catalog API** (Phase 24+):
  - Catalog domain model and repository (memory/SQLite)
  - Catalog REST API endpoints
  - Discovery commands, handlers, and registry
  - Discovery value objects

- **Extracted Port Interfaces**:
  - `AsyncTaskPort`, `BusPort`, `ConfigLoaderPort`, `SagaPort` in `application/ports/`
  - `ICatalogRepository`, `ICommandBus`, `IEventBusPort`, `IRuntimeStore` in `domain/contracts/`

- **Circuit Breaker HALF_OPEN**: State transition support with `CircuitBreakerStateChanged` event and event store compaction

- **Saga Compensation**: `schedule_command` support, `ProviderFailoverSaga` compensation steps, integration tests

- **Metrics History**: `MetricsHistoryStore`, snapshot worker, `/api/metrics/history` endpoint

### Fixed

- Thread-safety regression in `groups.py` rebalance
- Group member weight/priority defaults and strategy passthrough on update
- Group strategy enum, groups dict wiring, `normalizePath` trailing slash
- Missing `strategy` field in `UpdateGroupCommand`

### Changed

- Rate limit metrics exported to Prometheus (RESL-04)
- BLE001 exception hygiene across codebase (EXCP-02)
- Fuzz tests for input validation (TEST-02)

## [0.11.0] - 2026-03-08

### Added

- **Saga Persistence Foundation**: `SagaStateStore` with serialization/deserialization for durable saga state
  - Checkpoint integration in `SagaManager._handle_event` for crash recovery
  - Idempotency filter preventing duplicate event processing in sagas

- **Circuit Breaker Persistence**: Circuit breaker state survives restarts via `ProviderSnapshot` CB fields
  - Bootstrap wiring restores CB state on startup

- **Event Store Snapshots**: `IEventStore`, `SQLiteEventStore`, and `InMemoryEventStore` support snapshots
  - `EventSourcedProviderRepository` integrated with snapshot methods for faster aggregate hydration

- **Health Check Scheduling**: State-aware `BackgroundWorker` with adaptive health check intervals
  - `HealthTracker` jitter on backoff to prevent thundering herd
  - State-dependent check intervals (healthy vs degraded providers)

- **CommandBus Middleware Pipeline**: Extensible middleware support for cross-cutting concerns
  - `RateLimitMiddleware` wired into bootstrap for command-level rate limiting

- **Docker Discovery Resilience**: Reconnection with exponential backoff on Docker daemon failures

- **Property-Based Testing**: Hypothesis-powered state machine tests for Provider aggregate

- **PEP 561 Support**: `py.typed` marker for downstream type checking

### Fixed

- **Concurrency Safety**: `ProviderGroup` lock hierarchy violation (CONC-01) resolved
- **invoke_tool() Refresh**: Split into two-lock-cycle pattern (CONC-03) to avoid holding locks during I/O
- **ensure_ready()/_start()**: Restructured with `threading.Event` coordination for safer startup
- **Exception Hygiene**: All exception catches across domain, application, infrastructure, and server layers
  narrowed and annotated -- no more bare `except Exception` without justification
- **Type Safety**: Fixed mypy errors in `rate_limiter`, `gc`, and `docker_source`

### Changed

- Discovery pipeline now validates commands before provider registration
- `StdioClient` ordering invariant documented with regression tests

## [0.10.0] - 2026-03-01

### Added

- **Kubernetes Operator Controllers**:
  - `MCPProviderGroupReconciler` with label selection and status aggregation
  - `MCPDiscoverySourceReconciler` with 4 discovery modes
  - envtest integration tests for both controllers

- **Helm Chart Maturity**: Test templates and NOTES.txt for both charts, version bump to 0.10.0

- **Documentation Content**:
  - Configuration Reference page
  - MCP Tools Reference page
  - Provider Groups Guide
  - Facade API Guide
  - Updated mkdocs.yml navigation

### Changed

- Install URL updated to `mcp-hangar.io/install.sh`

### Removed

- `docs/security/AUTH_SECURITY_AUDIT.md` (superseded by inline security documentation)

## [0.9.0] - 2026-02-15

### Added

- **Timing Attack Prevention**: Constant-time API key validation using `hmac.compare_digest` across all auth stores
  - New `constant_time_key_lookup()` utility iterates all entries to prevent timing side-channel attacks
  - Applied to InMemory, SQLite, Postgres, and EventSourced stores
  - Timing verification tests confirm uniform lookup duration

- **Rate Limiter Exponential Backoff**: Lockout duration escalates with consecutive failures
  - Configurable `lockout_escalation_factor` (default: 2.0) and `max_lockout_seconds` (default: 3600)
  - New `RateLimitLockout` domain event emitted on IP lockout with duration and attempt count
  - New `RateLimitUnlock` domain event emitted on expiry, successful auth, or manual clear
  - Hardened cleanup worker for concurrent cleanup and timer drift edge cases

- **JWT Lifetime Enforcement**: Reject tokens with excessive lifetime (`exp - iat > max_token_lifetime`)
  - Configurable `max_token_lifetime` (default: 3600s, 0 to disable)
  - YAML config via `oidc.max_token_lifetime_seconds` or env var `MCP_JWT_MAX_TOKEN_LIFETIME`
  - New `TokenLifetimeExceededError` with clear diagnostic message including actual vs max lifetime
  - Missing `iat` or `exp` claims produce explicit `InvalidCredentialsError`

- **API Key Rotation**: Zero-downtime key rotation with configurable grace period
  - `IApiKeyStore.rotate_key(key_id, grace_period_seconds=86400, rotated_by="system")` contract
  - Old key remains valid during grace period (default: 24h), then raises `ExpiredCredentialsError`
  - New `KeyRotated` domain event with `key_id`, `new_key_id`, `rotated_at`, `grace_until`, `rotated_by`
  - Implemented in all 4 auth stores: InMemory, SQLite, Postgres, EventSourced
  - SQLite and Postgres stores include schema migrations adding `rotated_at`, `grace_until`, `replaced_by_key_id` columns
  - Guards against rotating revoked keys or double-rotating the same key

### Changed

- `AuthRateLimiter` now accepts optional `event_publisher` callback for domain event integration
- `InMemoryApiKeyStore` now accepts optional `event_publisher` callback
- `_AttemptTracker` tracks `lockout_count` for exponential backoff state
- `OIDCConfig` and `OIDCAuthConfig` include `max_token_lifetime` / `max_token_lifetime_seconds` fields
- `auth_bootstrap.py` passes `max_token_lifetime` to `OIDCConfig` during OIDC setup

## [0.8.0] - 2026-02-15

### Added

- **Tool Access Filtering**: Config-driven tool visibility control per provider, group, or member
  - `ToolAccessPolicy` value object with fnmatch glob pattern support (`*`, `?`, `[seq]`)
  - `ToolsConfig` dataclass for YAML configuration with `allow_list` and `deny_list`
  - `ToolAccessResolver` domain service with 3-level policy merge (provider -> group -> member)
  - Caching with automatic invalidation on policy changes
  - `ToolAccessDeniedError` exception for filtered tools (does not leak policy details)
  - Integration with hot-loading (`LoadProviderCommand.allow_tools/deny_tools`)
  - Integration with config reload (policies cleared and re-registered)
  - New Prometheus metrics: `mcp_hangar_tool_access_denied_total`, `mcp_hangar_tool_access_policy_evaluations_total`
  - Example config:

    ```yaml
    providers:
      grafana:
        tools:
          deny_list:
            - delete_*
            - create_alert_rule
    ```

- **Container Command Override**: Docker/Podman providers can now override container entrypoint
  - `container.command` — list of strings to override container entrypoint
  - `container.args` — additional arguments passed after command
  - Example config:

    ```yaml
    providers:
      custom:
        mode: docker
        image: my-mcp-server:latest
        container:
          command: ["python", "-m", "custom_entrypoint"]
          args: ["--verbose"]
    ```

### Changed

- `ProviderState` is now exported from `mcp_hangar.domain.model` module

## [0.7.0] - 2026-02-08

### Added

- **Facade `max_concurrency` config**: `HangarConfig.max_concurrency(n)` configures maximum parallel
  tool invocations through `Hangar.invoke()`. Default: 20, range: 1-100.
  - Also exposed in `HangarConfigData.max_concurrency` and `to_dict()` output
  - Constants `FACADE_DEFAULT_CONCURRENCY` (20) and `FACADE_MAX_CONCURRENCY` (100) exported from `facade` module
- **Two-level concurrency model**: New `ConcurrencyManager` with global and per-provider semaphores
  - Global semaphore limits total in-flight calls across all providers and batches (default: 50)
  - Per-provider semaphores limit concurrent calls to each individual provider (default: 10)
  - Consistent lock ordering (global-first, then provider) prevents deadlocks
  - All calls submitted to thread pool at once — no more sequential chunking into waves
  - Calls start as soon as any slot is free, enabling true parallel execution
- **Concurrency configuration**: New `execution` section in `config.yaml`
  - `execution.max_concurrency` — global limit across all providers
  - `execution.default_provider_concurrency` — default per-provider limit
  - Per-provider `max_concurrency` override in provider config
- **Concurrency observability**: New Prometheus metrics for concurrency control
  - `mcp_hangar_batch_inflight_calls` — global in-flight call gauge
  - `mcp_hangar_batch_inflight_calls_per_provider` — per-provider in-flight gauge
  - `mcp_hangar_batch_concurrency_wait_seconds` — histogram of slot acquisition wait time
  - `mcp_hangar_batch_concurrency_queued` — gauge of calls queued due to contention
- **Concurrency test suite**: 40 new unit tests covering limits, isolation, metrics, parallelism, thread safety, and backward compatibility

### Changed

- **Repository migration**: All URLs updated from `github.com/mapyr` to `github.com/mcp-hangar`
  - GitHub repository, container registry (GHCR), Go module paths, documentation links, Helm chart sources
- **BatchExecutor**: Integrated with `ConcurrencyManager` for cross-batch backpressure
- **Ruff/isort alignment**: Added `[tool.ruff.lint.isort]` config to root `pyproject.toml` so ruff I001 and standalone isort produce identical import ordering

### Fixed

- **Facade hardcoded concurrency limit**: `Hangar.invoke()` was hardcoded to 4 concurrent threads
  (`ThreadPoolExecutor(max_workers=4)`), causing parallel calls to execute in sequential waves of 4.
  Default increased to 20 and made configurable via `HangarConfig.max_concurrency()`. This masked the
  true parallelism benefits of the MCP provider architecture (e.g., 20 parallel 100ms calls took ~520ms
  instead of ~110ms).
- **Import ordering**: Fixed isort violations in `scripts/validate_config.py` and `examples/discovery/test_container_discovery.py`
- **E402 violations**: Moved mid-file imports to top of file in `examples/auth-keycloak/test_keycloak_integration.py`
- **B007 violation**: Renamed unused loop variable in `examples/auth-keycloak/test_oidc_local.py`

## [0.6.7] - 2026-02-06

### Fixed

- **ConfigReloadWorker tests**: Fixed timing issues in integration tests
  - `test_watchdog_detects_file_modification`: Increased watchdog initialization time and debounce wait
  - `test_multiple_rapid_changes_debounced_in_watchdog`: Added explicit polling interval configuration
  - `test_polling_detects_file_modification`: Ensured sufficient mtime difference for detection
- **CLI add provider test**: Fixed assertion to accept both uvx and npx package names
  - Test now correctly validates `mcp-server-fetch` (uvx) or `@modelcontextprotocol/server-fetch` (npx)

## [0.6.6] - 2026-02-06

### Added

- **Cookbook Documentation**: Step-by-step production recipes for MCP Hangar
  - Recipe 01 — HTTP Gateway: Single MCP provider behind Hangar as control plane
  - Recipe 02 — Health Checks: Automatic health monitoring with state transitions on failure
  - Recipe 03 — Circuit Breaker: Provider groups with circuit breaker for fast-fail protection
  - Recipe 04 — Failover: Automatic failover to backup provider with priority-based routing
  - All recipes include complete config, step-by-step Try It sections, and technical explanations
  - Recipes build on each other sequentially (01 → 02 → 03 → 04)
  - Each recipe validated with working configs and real Hangar tests
  - Located in `docs/cookbook/` with index and schema documentation

- **Hot-Reload Configuration**: Live configuration reloading without process restart
  - Automatic file watching via watchdog (inotify/fsevents) with polling fallback
  - SIGHUP signal handler for Unix-style reload
  - New MCP tool `hangar_reload_config` for interactive reload from AI assistant
  - Intelligent diff: only restarts providers with changed configuration
  - Unchanged providers preserve their state and active connections
  - Atomic reload: invalid configuration is rejected, current config preserved
  - New domain events: `ConfigurationReloadRequested`, `ConfigurationReloaded`, `ConfigurationReloadFailed`
  - New command: `ReloadConfigurationCommand` with CQRS handler
  - Background worker `ConfigReloadWorker` for automatic file monitoring
  - Configurable via `config_reload` section in config.yaml

- **Init Dependency Detection**: `mcp-hangar init` now detects available runtimes before offering providers
  - Step 0 checks for `npx`, `uvx`, `docker`, `podman` in PATH
  - Providers filtered by available dependencies (npx-based providers hidden when Node.js not installed)
  - Clear error message with install instructions when no runtimes found
  - Unavailable providers shown grayed out with "(requires npx)" hint
  - Bundles automatically filtered to only include installable providers
  - New module: `dependency_detector.py` with `DependencyStatus`, `detect_dependencies()`

- **Init Smoke Test**: `mcp-hangar init` now tests providers after configuration
  - Step 5 starts each provider and waits for READY state (max 10s total)
  - Shows green checkmark per provider on success: `✓ filesystem ready (1234ms)`
  - Shows detailed error with actionable suggestion on failure
  - Summary shows pass/fail count before "Restart Claude Desktop" prompt
  - Skip with `--skip-test` flag if needed
  - New module: `smoke_test.py` with `run_smoke_test()`, `SmokeTestResult`

- **Init Existing Config Handling**: `mcp-hangar init` now handles existing configuration safely
  - Interactive mode prompts with three options: Merge, Backup & Overwrite, Abort
  - Merge: Adds new providers while preserving existing ones (no overwrites)
  - Backup & Overwrite: Creates timestamped backup, then replaces with new config
  - Abort: Cancels init, preserves existing configuration unchanged
  - Non-interactive mode (`-y`): Always creates backup then overwrites
  - `--reset` flag: Overwrites without backup or prompt
  - Never silently overwrites existing configuration
  - New method: `ConfigFileManager.merge_providers()` for safe merging

- **Init uvx Support (Dual-Stack)**: `mcp-hangar init` now supports uvx as alternative to npx
  - Providers with Python equivalents can now run via uvx when Node.js not available
  - Runtime priority: uvx > npx (dogfooding - MCP Hangar is Python-based)
  - Mapping: `npx @modelcontextprotocol/server-fetch` -> `uvx mcp-server-fetch`
  - All starter providers (filesystem, fetch, memory) have uvx packages
  - Config generates appropriate command based on detected runtimes
  - Provider unavailable only if NO suitable runtime available
  - puppeteer remains npx-only (no Python equivalent)
  - New fields in `ProviderDefinition`: `uvx_package`, `get_preferred_runtime()`, `get_command_package()`

- **One-Liner Quick Start**: Zero-interaction installation and setup
  - New install script at `scripts/install.sh` (hosted at mcp-hangar.io/install.sh)
  - Full happy path: `curl -sSL https://mcp-hangar.io/install.sh | bash && mcp-hangar init -y && mcp-hangar serve`
  - Auto-detects uv/pip, installs package, verifies installation
  - `init -y` uses starter bundle with detected runtime (uvx preferred)
  - Works on clean Mac/Linux with Python 3.11+ and uvx or npx
  - Updated README with prominent quick start section

### Configuration

New `config_reload` section in config.yaml:

```yaml
config_reload:
  enabled: true       # default: true
  use_watchdog: true  # default: true, falls back to polling
  interval_s: 5       # polling interval when watchdog unavailable
```

### Documentation

- New cookbook documentation: `docs/cookbook/` with 4 production recipes
- New reference documentation: `docs/reference/hot-reload.md`

## [0.6.5] - 2026-02-03

### Added

- **Metrics Population**: Prometheus metrics now emit data from domain events
  - Provider state metrics: `mcp_hangar_provider_state`, `mcp_hangar_provider_up`, `mcp_hangar_provider_starts_total`, `mcp_hangar_provider_stops_total`
  - Tool call metrics: `mcp_hangar_tool_calls_total`, `mcp_hangar_tool_call_duration_seconds`, `mcp_hangar_tool_call_errors_total`
  - Health check metrics: `mcp_hangar_health_checks_total`, `mcp_hangar_health_check_duration_seconds`, `mcp_hangar_health_check_consecutive_failures`
  - Rate limiter metrics: `mcp_hangar_rate_limit_hits_total`
  - HTTP client metrics: `mcp_hangar_http_requests_total`, `mcp_hangar_http_request_duration_seconds`, `mcp_hangar_http_errors_total`
  - `MetricsEventHandler` bridges domain events to Prometheus
  - HTTP client instrumented with provider label support

### Fixed

- Metrics that were defined but never populated now emit data correctly
- Tool descriptions improved for LLM clarity (previous commit in 0.6.4)

## [0.6.4] - 2026-02-03

### Added

- **Observability Bootstrap Integration**: Tracing and Langfuse initialization during application startup
  - New `observability.py` module in bootstrap package
  - OpenTelemetry tracing initialized during bootstrap
  - Langfuse adapter initialization during bootstrap
  - `ObservabilityAdapter` stored in `ApplicationContext`
  - Proper shutdown sequence for tracing and Langfuse

### Changed

- **Alerts**: Reduced from 28 to 19 alerts (removed 9 using non-existent metrics)
  - Added: `MCPHangarCircuitBreakerTripped`, `MCPHangarProviderUnhealthy`, `MCPHangarHealthCheckSlow`
  - Adjusted thresholds: P95 latency 5s->3s, P99 10s->5s, batch slow 60s->30s
  - Removed alerts referencing `provider_state`, `provider_up`, `discovery_*` (not yet populated)

### Documentation

- Complete rewrite of `docs/guides/OBSERVABILITY.md`
  - Documented "Currently Exported Metrics" vs "Metrics Not Yet Implemented"
  - Updated alert tables to match actual `alerts.yaml`
  - Fixed PromQL examples with correct metric names
  - Added production readiness checklist

### Added (Dashboards)

- New `alerts.json` Grafana dashboard for alert monitoring
- New `provider-details.json` Grafana dashboard for per-provider deep dive

## [0.6.3] - 2026-02-01

### Added

- **Response Truncation System**: Smart truncation for batch responses exceeding context limits
  - Configurable maximum batch response size (default ~900KB, safely under Claude's 1MB limit)
  - Proportional budget allocation across batch results based on original size
  - Smart JSON truncation preserving structure (dicts keep keys, lists truncate from end)
  - Line boundary awareness for text truncation
  - Full response caching with continuation IDs for later retrieval
  - Memory cache (LRU with TTL) and Redis cache backends
  - New MCP tools:
    - `hangar_fetch_continuation` - Retrieve full/remaining content from truncated response
    - `hangar_delete_continuation` - Manually delete cached continuation
  - New value objects: `TruncationConfig`, `ContinuationId`
  - New domain contract: `IResponseCache` with `MemoryResponseCache` and `RedisResponseCache` implementations
  - Opt-in via configuration (disabled by default)

### Configuration

New `truncation` section in config.yaml:

```yaml
truncation:
  enabled: true                      # Opt-in, default false
  max_batch_size_bytes: 950000       # ~950KB (under 1MB limit)
  min_per_response_bytes: 10000      # 10KB minimum per response
  cache_ttl_s: 300                   # 5 minutes
  cache_driver: memory               # memory | redis
  redis_url: redis://localhost:6379  # Required if redis
  max_cache_entries: 10000
  preserve_json_structure: true
  truncate_on_line_boundary: true
```

## [0.6.2] - 2026-01-31

### Changed

- **Unified tool naming**: All MCP tools now use `hangar_*` prefix for consistency
  - `registry_tools` -> `hangar_tools`
  - `registry_details` -> `hangar_details`
  - `registry_warm` -> `hangar_warm`
  - `registry_health` -> `hangar_health`
  - `registry_metrics` -> `hangar_metrics`
  - `registry_discover` -> `hangar_discover`
  - `registry_discovered` -> `hangar_discovered`
  - `registry_quarantine` -> `hangar_quarantine`
  - `registry_approve` -> `hangar_approve`
  - `registry_sources` -> `hangar_sources`
  - `registry_group_list` -> `hangar_group_list`
  - `registry_group_rebalance` -> `hangar_group_rebalance`

- Updated error hints and recovery messages to use new tool names
- Updated docs/guides/DISCOVERY.md with new tool names

### Refactoring

- **Bootstrap modularization**: Split `server/bootstrap.py` (890 LOC) into focused modules
  - `server/bootstrap/__init__.py` - Main bootstrap orchestration
  - `server/bootstrap/cqrs.py` - Command/query handler registration
  - `server/bootstrap/discovery.py` - Discovery source configuration
  - `server/bootstrap/event_handlers.py` - Event handler setup
  - `server/bootstrap/event_store.py` - Event store initialization
  - `server/bootstrap/hot_loading.py` - Hot-loading configuration
  - `server/bootstrap/knowledge_base.py` - Knowledge base setup
  - `server/bootstrap/tools.py` - MCP tool registration
  - `server/bootstrap/workers.py` - Background worker creation

- **Batch tool modularization**: Split `server/tools/batch.py` (952 LOC) into focused modules
  - `server/tools/batch/__init__.py` - Public API (`hangar_call`)
  - `server/tools/batch/executor.py` - Batch execution engine
  - `server/tools/batch/models.py` - Data classes and constants
  - `server/tools/batch/validator.py` - Validation logic

- **Provider launcher modularization**: Split `domain/services/provider_launcher.py` into package
  - `domain/services/provider_launcher/__init__.py` - Public API
  - `domain/services/provider_launcher/base.py` - Base launcher interface
  - `domain/services/provider_launcher/subprocess.py` - Subprocess launcher
  - `domain/services/provider_launcher/docker.py` - Docker launcher
  - `domain/services/provider_launcher/container.py` - Container utilities
  - `domain/services/provider_launcher/http.py` - HTTP/SSE launcher
  - `domain/services/provider_launcher/factory.py` - Launcher factory

### Migration

If you have scripts or integrations using the old `registry_*` tool names, update them to use `hangar_*`:

```python
# Before
registry_tools(provider="math")
registry_health()

# After
hangar_tools(provider="math")
hangar_health()
```

## [0.6.0] - 2026-01-31

### Added

- **Interactive CLI**: New typer-based CLI for streamlined MCP provider management
  - `hangar init` - Initialize new project with guided setup
  - `hangar add <provider>` - Add providers interactively with auto-configuration
  - `hangar remove <provider>` - Remove providers from configuration
  - `hangar status` - Show current providers and their states
  - `hangar serve` - Start the MCP server (default command)
  - `hangar completion` - Generate shell completion scripts
  - Rich console output with colors and progress indicators
  - JSON output mode for scripting (`--json`)
  - Backward compatible with existing argparse CLI

- **Provider Bundles**: Pre-configured provider definitions for quick setup
  - Built-in definitions for popular MCP servers (filesystem, memory, sqlite, fetch, github, slack, etc.)
  - `InstallType` enum: NPX, UVX, DOCKER, BINARY
  - `ConfigType` enum: NONE, PATH, SECRET, STRING, URL
  - Bundle resolver for discovering and validating providers

- **Multi-runtime Installers**: Pluggable installer infrastructure
  - `NpmInstaller` - Install providers via npx
  - `PyPIInstaller` - Install providers via uvx
  - `OCIInstaller` - Pull and run Docker/OCI images
  - `BinaryInstaller` - Download and execute pre-built binaries
  - Automatic runtime detection and validation

- **Package Resolver**: Unified package resolution across ecosystems
  - Resolve provider packages from npm, PyPI, or container registries
  - Version validation and compatibility checks

- **Secrets Resolver**: Secure configuration management
  - Environment variable interpolation (`${VAR_NAME}`)
  - Support for secret references in provider configs
  - Integration with system keychain (future)

- **Output Redactor**: Automatic sensitive data redaction
  - Redact API keys, tokens, and passwords from logs
  - Configurable redaction patterns
  - Safe for production logging

- **Runtime Store**: Persistent storage for installed provider runtimes
  - Track installed providers and their versions
  - Cache validation and cleanup

### Changed

- Refactored CLI into modular command structure under `server/cli/`
- Legacy CLI preserved in `cli_legacy.py` for backward compatibility
- Provider launcher now supports multiple install types

### Documentation

- Updated quickstart guide with new CLI commands

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
- **Quick Install Script**: `curl -sSL https://mcp-hangar.io/install.sh | bash`

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

[Unreleased]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.7...v0.7.0
[0.6.7]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.6...v0.6.7
[0.6.6]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.5...v0.6.6
[0.6.5]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.0...v0.6.2
[0.6.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mcp-hangar/mcp-hangar/releases/tag/v0.1.0

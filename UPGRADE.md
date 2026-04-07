# Upgrading to MCP Hangar v1.0

This guide covers upgrading from v0.12.x (or earlier) to v1.0.0. If you are
upgrading from a version older than v0.4.0, read every section. If you are
already on v0.12.x, skip to the [Pre-flight checklist](#pre-flight-checklist)
and then review only the sections marked with your starting version.

---

## Pre-flight checklist

Run through this list before you begin. Every item should be green before you
upgrade production.

1. **Back up your configuration.** Copy `config.yaml`, `.env`, and any
   Kubernetes manifests (MCPProvider, MCPProviderGroup, MCPDiscoverySource CRs).
2. **Back up your event store.** If you use SQLite or Postgres event sourcing,
   take a snapshot or dump before upgrading.
3. **Note your current version.** Run `mcp-hangar --version` or check
   `pyproject.toml`.
4. **Check Python version.** v1.0 requires Python 3.11+.
   Run `python3 --version` to confirm.
5. **Review deprecation warnings.** Run your test suite and check logs for
   deprecation warnings introduced in v0.4.0 through v0.12.0.
6. **Read the sections below** that apply to your starting version.
7. **Test in staging** before promoting to production.

---

## Version upgrade paths

| Starting version | Path |
|-----------------|------|
| v0.1.x - v0.3.x | Read ALL sections below in order. |
| v0.4.x - v0.6.x | Start at [Environment variables](#environment-variables). |
| v0.7.x - v0.12.x | Start at [Configuration changes](#configuration-changes-v060). |
| v0.12.x | Start at [Enterprise module split](#enterprise-module-split). |

---

## Python version requirement

**Applies to:** all versions before v0.3.0

MCP Hangar v1.0 requires Python 3.11 or later. Earlier versions were compatible
with Python 3.10. If you are running 3.10, upgrade Python first.

```bash
python3 --version
# Must be 3.11.x or later
```

---

## Rebrand: "registry" to "hangar" (v0.4.0)

**Applies to:** upgrading from v0.3.x or earlier

v0.4.0 renamed the project from "MCP Registry" to "MCP Hangar". This is the
single largest breaking change in the project's history. All backward
compatibility aliases were removed in v0.4.0.

### MCP tool renames

All 14 MCP tools changed prefix from `registry_*` to `hangar_*`:

| Old (removed) | New |
|---------------|-----|
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

**Action:** Update any AI assistant system prompts, scripts, or integrations
that reference tool names.

### Python API renames

| Old (removed) | New |
|---------------|-----|
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
| `with_registry()` | `with_hangar()` |
| `factory.registry` | `factory.hangar` |

**Action:** Search your code for `Registry` and `with_registry` and replace.

### Removed factory functions

These convenience functions were removed in v0.4.0:

| Removed | Replacement |
|---------|-------------|
| `setup_fastmcp_server()` | `MCPServerFactory` |
| `create_fastmcp_server()` | `MCPServerFactory.create_server()` |
| `run_fastmcp_server()` | `MCPServerFactory.create_asgi_app()` |

```python
# Before (removed)
from mcp_hangar import setup_fastmcp_server
server = setup_fastmcp_server(config_path="config.yaml")

# After
from mcp_hangar.fastmcp_server import MCPServerFactory
factory = MCPServerFactory()
server = factory.create_server(config_path="config.yaml")
```

### Prometheus metric renames

All metrics changed prefix from `mcp_registry_*` to `mcp_hangar_*`:

| Old | New |
|-----|-----|
| `mcp_registry_tool_calls_total` | `mcp_hangar_tool_calls_total` |
| `mcp_registry_tool_call_duration_seconds` | `mcp_hangar_tool_call_duration_seconds` |
| `mcp_registry_provider_state` | `mcp_hangar_provider_state` |
| `mcp_registry_cold_starts_total` | `mcp_hangar_cold_starts_total` |
| `mcp_registry_health_checks` | `mcp_hangar_health_checks` |
| `mcp_registry_circuit_breaker_state` | `mcp_hangar_circuit_breaker_state` |

**Action:** Update Grafana dashboards, Prometheus recording rules, and alert
rules. If you use the bundled dashboards from `monitoring/`, update them from
the latest release.

---

## Kubernetes operator API group (v0.2.0)

**Applies to:** upgrading from v0.1.x

The CRD API group changed from `mcp.hangar.io` to `mcp-hangar.io` in v0.2.0.

```yaml
# Before (v0.1.x)
apiVersion: mcp.hangar.io/v1alpha1
kind: MCPProvider

# After (v0.2.0+)
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPProvider
```

**Action:**

1. Update all MCPProvider, MCPProviderGroup, and MCPDiscoverySource manifests.
2. Delete old CRDs: `kubectl delete crd mcpproviders.mcp.hangar.io`
3. Install new CRDs from the updated Helm chart or `make install` in the
   operator directory.
4. Re-apply your custom resources with the new API group.

---

## Environment variables

**Applies to:** all versions

### Prefix migration: HANGAR\_\* to MCP\_\*

The canonical environment variable prefix is `MCP_*`. The old `HANGAR_*` prefix
is not supported in v1.0.

| Old | New |
|-----|-----|
| `HANGAR_CONFIG` | `MCP_CONFIG` |
| `HANGAR_MODE` | `MCP_MODE` |
| `HANGAR_HTTP_HOST` | `MCP_HTTP_HOST` |
| `HANGAR_HTTP_PORT` | `MCP_HTTP_PORT` |
| `HANGAR_LOG_LEVEL` | `MCP_LOG_LEVEL` |
| `HANGAR_JSON_LOGS` | `MCP_JSON_LOGS` |

**Action:** Search your shell profiles, `.env` files, Docker Compose files,
Kubernetes ConfigMaps/Secrets, and CI pipelines for `HANGAR_` and replace with
`MCP_`.

### Langfuse environment variables

The Langfuse integration variables also follow the `MCP_*` convention in v1.0:

| Old | New |
|-----|-----|
| `HANGAR_LANGFUSE_ENABLED` | `MCP_LANGFUSE_ENABLED` |
| `HANGAR_LANGFUSE_SAMPLE_RATE` | `MCP_LANGFUSE_SAMPLE_RATE` |
| `HANGAR_LANGFUSE_SCRUB_INPUTS` | `MCP_LANGFUSE_SCRUB_INPUTS` |
| `HANGAR_LANGFUSE_SCRUB_OUTPUTS` | `MCP_LANGFUSE_SCRUB_OUTPUTS` |

### License key

| Old | New |
|-----|-----|
| `HANGAR_LICENSE_KEY` | `MCP_LICENSE_KEY` |

---

## Repository URL migration (v0.7.0)

**Applies to:** upgrading from v0.6.x or earlier

All repository URLs migrated from `github.com/mapyr` to
`github.com/mcp-hangar` in v0.7.0. This affects:

- Git remote URLs
- Go module import paths
- Container image references (GHCR)
- Documentation links
- Helm chart source URLs

**Action:** Update any pinned references to the old GitHub organization.

```bash
# Check for old references
grep -r "mapyr" . --include="*.yaml" --include="*.yml" --include="*.toml"

# Go modules: update go.mod
# Old: github.com/mapyr/...
# New: github.com/mcp-hangar/...
```

---

## Configuration changes (v0.6.0+)

Several new configuration sections were added between v0.6.0 and v0.8.0. These
are all opt-in with sensible defaults, so existing config files continue to
work. Review these if you want to take advantage of new capabilities.

### Hot-reload configuration (v0.6.6)

```yaml
# New section -- optional, enabled by default
config_reload:
  enabled: true
  use_watchdog: true
  interval_s: 5
```

### Response truncation (v0.6.3)

```yaml
# New section -- optional, disabled by default
truncation:
  enabled: false
  max_batch_size_bytes: 950000
  cache_driver: memory        # memory | redis
  cache_ttl_s: 300
```

### Execution concurrency (v0.7.0)

```yaml
# New section -- optional
execution:
  max_concurrency: 50              # global limit
  default_provider_concurrency: 10 # per-provider default

providers:
  my_provider:
    max_concurrency: 5  # per-provider override
```

### Tool access filtering (v0.8.0)

```yaml
# New per-provider section -- optional
providers:
  grafana:
    tools:
      deny_list:
        - "delete_*"
        - "create_alert_rule"
      allow_list:
        - "query_*"
```

---

## bootstrap() API change (v0.3.0)

**Applies to:** upgrading from v0.2.x or earlier

The `bootstrap()` function now accepts an optional `config_dict` parameter for
programmatic configuration. This is backward compatible -- existing calls
without the parameter continue to work. If you were monkey-patching
configuration, use this parameter instead:

```python
# Before
import mcp_hangar.server.config as cfg
cfg._global_config = my_config
bootstrap()

# After
bootstrap(config_dict=my_config)
```

---

## Enterprise module split

**Applies to:** v1.0 (new in this release)

Starting with v0.13.0, enterprise features (auth, RBAC, behavioral profiling,
compliance export, Langfuse integration) are moving from the core package to the
`enterprise/` directory under BSL 1.1 licensing.

### What moved

| Feature | Old location | New location |
|---------|-------------|--------------|
| API key stores, JWT/OIDC, RBAC | `src/mcp_hangar/infrastructure/auth/` | `enterprise/auth/` |
| Role definitions | `src/mcp_hangar/domain/security/roles.py` | `enterprise/auth/roles.py` |
| Auth REST endpoints | `src/mcp_hangar/server/api/auth/` | `enterprise/auth/api/` |
| Auth bootstrap wiring | `src/mcp_hangar/server/auth_bootstrap.py` | `enterprise/auth/bootstrap.py` |
| Tool access policy enforcement | `src/mcp_hangar/domain/value_objects/tool_access_policy.py` | `enterprise/policies/` (interface stays in core) |
| SQLite/Postgres event stores | `src/mcp_hangar/infrastructure/persistence/event_store.py` | `enterprise/persistence/` |
| Langfuse integration | `src/mcp_hangar/infrastructure/observability/langfuse_adapter.py` | `enterprise/integrations/langfuse.py` |

### Impact on deployments

- **Open source users (MIT):** No impact. Core features (provider lifecycle,
  health checks, circuit breaker, groups, load balancing, failover, Prometheus
  metrics, OTEL export, CLI, hot-reload, batch invocations) remain in the MIT
  core.
- **Enterprise users (BSL):** Set the `MCP_LICENSE_KEY` environment variable.
  The bootstrap process automatically loads enterprise modules when a valid
  license key is present. Without the key, enterprise features are replaced by
  no-op implementations.

### Import boundary

Core code never imports from `enterprise/`. If you have custom code that imports
from internal paths that moved to `enterprise/`, update your imports:

```python
# Before (if you imported internal auth modules directly)
from mcp_hangar.infrastructure.auth.api_key_store import SQLiteApiKeyStore

# After -- use the contract interface from core
from mcp_hangar.domain.contracts import IApiKeyStore
# The concrete implementation is loaded by bootstrap when licensed
```

---

## Deprecated patterns removed in v1.0

The following were deprecated in earlier versions and are removed in v1.0:

| Deprecated | Replacement | Removed in |
|-----------|-------------|------------|
| `provider_manager.py` | `Provider` aggregate | v1.0 |
| `ProviderSpec` | `Provider` constructor | v1.0 |
| `ProviderConnection` | `Provider` aggregate | v1.0 |
| `ProviderHealth` in `models.py` | `HealthTracker` | v1.0 |
| `setup_fastmcp_server()` | `MCPServerFactory` | v0.4.0 |
| `create_fastmcp_server()` | `MCPServerFactory.create_server()` | v0.4.0 |
| `run_fastmcp_server()` | `MCPServerFactory.create_asgi_app()` | v0.4.0 |
| `RegistryFunctions` | `HangarFunctions` | v0.4.0 |
| `with_registry()` | `with_hangar()` | v0.4.0 |

**Action:** Search your code for these names. If any are found, replace them
before upgrading.

```bash
# Quick check for deprecated patterns
grep -rn "ProviderSpec\|ProviderConnection\|ProviderHealth\|provider_manager" \
  --include="*.py" your_project/

grep -rn "setup_fastmcp_server\|create_fastmcp_server\|run_fastmcp_server" \
  --include="*.py" your_project/

grep -rn "RegistryFunctions\|with_registry\|registry_list\|registry_invoke" \
  --include="*.py" your_project/
```

---

## Kubernetes operator upgrade

**Applies to:** users running the MCP Hangar operator in Kubernetes

### CRD updates

The operator CRDs remain at `v1alpha1` in v1.0. A future release will
introduce `v1beta1` with conversion webhooks (tracked as task 11.10).

If upgrading from v0.1.x, you must update the API group as described in
[Kubernetes operator API group](#kubernetes-operator-api-group-v020).

### Helm chart upgrade

```bash
# 1. Back up current values
helm get values mcp-hangar -n mcp-hangar > values-backup.yaml

# 2. Update the chart repository
helm repo update mcp-hangar

# 3. Review changes
helm diff upgrade mcp-hangar mcp-hangar/mcp-hangar \
  -n mcp-hangar -f values-backup.yaml

# 4. Apply
helm upgrade mcp-hangar mcp-hangar/mcp-hangar \
  -n mcp-hangar -f values-backup.yaml
```

### Helm values changes

Review your `values.yaml` for these additions in the hangar-cloud chart:

```yaml
# Authentication (required for enterprise features)
config:
  auth:
    jwtSecret: ""  # Set via secret reference, not plaintext

# Database (if using Postgres event store)
postgresql:
  enabled: true

# Autoscaling (new)
autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
```

---

## Observability upgrade

### Grafana dashboards

If you use the bundled Grafana dashboards from `monitoring/`, replace them with
the versions from v1.0. Key changes since v0.4.0:

- All metric names use `mcp_hangar_*` prefix (not `mcp_registry_*`).
- New dashboards: `alerts.json`, `provider-details.json` (added v0.6.4).
- Alert count reduced from 28 to 19 in v0.6.4 (removed alerts for
  not-yet-populated metrics).
- Updated thresholds: P95 latency 5s to 3s, P99 10s to 5s, batch slow 60s to
  30s.

### Prometheus alert rules

Replace `monitoring/alerts.yaml` with the v1.0 version. If you have custom
rules, update metric names:

```yaml
# Before
- alert: MCPRegistryToolCallSlow
  expr: mcp_registry_tool_call_duration_seconds > 5

# After
- alert: MCPHangarToolCallSlow
  expr: mcp_hangar_tool_call_duration_seconds > 5
```

### New metrics (v0.5.0 - v0.12.0)

These metrics were added after v0.4.0. They are available automatically -- no
configuration change needed, but you may want to add dashboard panels:

| Metric | Added in | Description |
|--------|----------|-------------|
| `mcp_hangar_batch_calls_total` | v0.5.0 | Batch invocation count |
| `mcp_hangar_batch_duration_seconds` | v0.5.0 | Batch execution time |
| `mcp_hangar_batch_concurrency_gauge` | v0.5.0 | Current parallel executions |
| `mcp_hangar_batch_inflight_calls` | v0.7.0 | Global in-flight call gauge |
| `mcp_hangar_batch_concurrency_wait_seconds` | v0.7.0 | Slot acquisition wait time |
| `mcp_hangar_tool_access_denied_total` | v0.8.0 | Tool access policy denials |
| `mcp_hangar_tool_access_policy_evaluations_total` | v0.8.0 | Policy evaluations |
| `mcp_hangar_rate_limit_hits_total` | v0.6.5 | Rate limiter triggers |
| `mcp_hangar_http_requests_total` | v0.6.5 | HTTP client requests |

---

## Step-by-step upgrade procedure

### PyPI package users

```bash
# 1. Check current version
pip show mcp-hangar

# 2. Upgrade
pip install --upgrade mcp-hangar==1.0.0
# or with uv:
uv pip install mcp-hangar==1.0.0

# 3. Verify
mcp-hangar --version

# 4. Test configuration
mcp-hangar serve --dry-run  # if available, or start and check logs

# 5. Update environment variables (see sections above)
# 6. Update any custom code imports (see sections above)
# 7. Restart
mcp-hangar serve
```

### Docker users

```bash
# 1. Pull new image
docker pull ghcr.io/mcp-hangar/mcp-hangar:1.0.0

# 2. Update docker-compose.yml image tag
# image: ghcr.io/mcp-hangar/mcp-hangar:1.0.0

# 3. Update environment variables in docker-compose.yml
# Replace HANGAR_* with MCP_*

# 4. Restart
docker compose up -d
```

### Kubernetes users

```bash
# 1. Back up CRDs and custom resources
kubectl get mcpproviders -A -o yaml > mcpproviders-backup.yaml
kubectl get mcpprovidergroups -A -o yaml > mcpprovidergroups-backup.yaml
kubectl get mcpdiscoverysources -A -o yaml > mcpdiscoverysources-backup.yaml

# 2. Update Helm chart
helm repo update
helm upgrade mcp-hangar mcp-hangar/mcp-hangar -n mcp-hangar -f values.yaml

# 3. Verify operator is running
kubectl get pods -n mcp-hangar
kubectl logs -n mcp-hangar deploy/mcp-hangar-operator

# 4. Verify CRDs
kubectl get crd | grep mcp-hangar

# 5. Check provider status
kubectl get mcpproviders -A
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'mcp_hangar.provider_manager'"

The `provider_manager` module was removed. See
[Deprecated patterns removed in v1.0](#deprecated-patterns-removed-in-v10).

### "NameError: name 'RegistryFunctions' is not defined"

The old registry names were removed in v0.4.0. See
[Rebrand: registry to hangar](#rebrand-registry-to-hangar-v040).

### "Unknown environment variable HANGAR_*"

v1.0 only reads `MCP_*` variables. See
[Environment variables](#environment-variables).

### Enterprise features not loading

Ensure `MCP_LICENSE_KEY` is set (not `HANGAR_LICENSE_KEY`). Check bootstrap
logs for "enterprise modules loaded" or "no license key found, using defaults".

### CRD conflicts after operator upgrade

If old CRDs from the `mcp.hangar.io` API group remain, delete them manually:

```bash
kubectl delete crd mcpproviders.mcp.hangar.io
kubectl delete crd mcpprovidergroups.mcp.hangar.io
kubectl delete crd mcpdiscoverysources.mcp.hangar.io
```

Then reinstall CRDs from the updated chart.

---

## Getting help

- GitHub Issues: https://github.com/mcp-hangar/mcp-hangar/issues
- Changelog: See `CHANGELOG.md` for the complete version history.
- Architecture: See `ARCHITECTURE.md` for system design documentation.

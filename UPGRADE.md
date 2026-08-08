# Upgrading MCP Hangar

## 2.5.0 — nothing changes until you select a storage backend

The release adds `persistence.backend` and multi-replica coordination. **An
existing configuration that sets neither is unaffected**: omitting `persistence`
keeps the per-subsystem storage behaviour exactly as it was, which is deliberate
— a storage rewiring must not change what a running deployment does.

Everything below applies only once you opt in.

### Selecting a backend takes over every persisted concern

`persistence.backend: sqlite | postgresql` chooses storage for all of it at
once: the event log and its delivery mark, server configuration, the audit
trail, saga state, approvals, API keys, roles, tool-access policies, metric
history and the management lease. A backend serves every one of them or
selection is refused, which is what makes the half-configured deployment
unrepresentable — before 2.5.0 you could select the PostgreSQL auth driver and
silently lose tool-access policy management with it.

Two consequences to check before you roll out:

- **`${VAR}` in configuration is interpolated everywhere.** It used to work
  inside `mcp_servers.<id>.auth` and nowhere else, while the documentation
  described it as a property of configuration. If you kept a secret out of the
  file the way the production checklist says to, and it silently arrived as the
  literal characters, this is why. The refusal moved with it: a `${VAR}` with no
  value and no `:-default` has always been fail-closed, and now fails the whole
  boot rather than only the `auth` sub-block. Check the keys you never had to
  set before -- `${VAR:-}` allows an empty value explicitly. A value that
  *contains* a literal `${...}`, such as a generated password, is safe: the
  document is interpolated once, so the substituted text is never rescanned.
- **A per-subsystem key that names a different backend now refuses startup.**
  `auth.storage.driver` and `event_store.driver` are compared against your
  selection, and a contradiction fails the boot rather than being resolved by a
  precedence rule. Whichever way such a rule fell, half of what you wrote would
  be ignored — and the half that loses is the one written most recently.
  `memory` is exempt: it is a testing choice, not a storage backend.
- **`event_store.allow_memory_fallback` no longer has anything to decide.** With
  a backend selected, the log and its delivery mark come from it, and a backend
  is durable as a whole. Keep the key if you are not selecting a backend; it
  still fails a non-durable store fast there.

**There is no migration between backends.** Selecting PostgreSQL on a gateway
that has been running on SQLite starts an empty database — it does not move
what is in the file.

### Selecting PostgreSQL turns coordination on, at one replica

This is the one that can surprise a single-node deployment. Coordination keys
off whether the storage **can be shared**, not off how many replicas you run, so
a single gateway on PostgreSQL takes a management lease and reports
`coordinates_with_peers: true`. It manages the fleet, because it is the holder —
nothing stops working.

What does change on that deployment: **registering a `subprocess`, `docker` or
`container` server through the API is refused** (HTTP 422), because those modes
attach a child process's stdio to one gateway and any peer that learned of such
a server would start its own copy. Servers already declared in `config.yaml`
keep working — the refusal is on the registration path, not the startup one.

If that deployment is genuinely single-node and wants to keep registering local
modes at runtime, stay on `persistence.backend: sqlite`, which is not shareable
and therefore not coordinated.

The message says so: it names the condition — storage peers can share — and
offers `persistence.backend: sqlite` as the alternative to `remote` mode. It
used to end "or run a single instance", which read oddly when you already were
one.

### A declared cluster refuses a child-process server outright

The paragraph above is about *runtime registration*. Servers declared in
`config.yaml` take a different path, and when the deployment declares a
`coordination:` block they are now refused **at startup**, naming every
offender at once:

```
this gateway is configured as part of a cluster (`coordination:`), and
'reports' is 'subprocess'. ... Use `remote` mode for servers several replicas
must serve, or remove the `coordination:` block to run this as a single gateway.
```

Without the block nothing here fires, which is the whole point of asking on
that axis: a single gateway that merely uses PostgreSQL keeps running its child
processes exactly as before.

What this replaced is worth knowing if you ran an earlier candidate. Such a
server loaded on every replica and only the lease holder could start it, so
`GET /api/mcp_servers/<id>/tools` answered with the server's tools on one pod
and an empty list on the others, and starting it on any other pod returned a
`409`.

### A `coordination:` block requires PostgreSQL

Adding `coordination:` is the statement that these replicas are meant to be
**one** gateway. On a file-backed backend it refuses to start, because replicas
that cannot share storage are not a cluster — each would hold its own fleet and
its own lease and never notice the others. That is not hypothetical: three
replicas on SQLite each reported `manages_fleet: true`, with every health check
green.

Running many pods each with their own storage stays legitimate — that is many
gateways, and nobody's business but yours. What is refused is calling them one.

### If you already run more than one replica

Through 2.4.0 the documentation said not to, and the failure was silent rather
than loud. On 2.5.0, to make a replica set safe you need all three of: one
PostgreSQL every replica shares, a `coordination:` block, and `remote`-mode
servers. Then check it pod by pod rather than through the Service — exactly one
should answer `manages_fleet: true` at `GET /api/system`.

Two costs are worth knowing before the rollout rather than after: rate limits
are counted **per instance** (three replicas admit three times the configured
rate — a fleet-wide cap belongs at the ingress), and anything travelling by the
shared log reaches peers within a poll interval rather than immediately.

The full recipe is [cookbook 25](https://mcp-hangar.io/docs/cookbook/25-multiple-replicas);
the decisions and their failure modes are in
[ADR-020](https://mcp-hangar.io/docs/adr/ADR-020-high-availability).

## Discovery: namespace policy moves to the Kubernetes source

`discovery.security.allowed_namespaces` and `discovery.security.denied_namespaces`
belong to the Kubernetes source now, not to the core's security config. They
were the only source-specific rules in a component that is otherwise
source-agnostic, and they were applied behind a check on the source's name --
so any other source passed that stage with nothing validated and nothing said.

**Who is affected:** deployments that set either key.

**What to do:** move them under the kubernetes source's own entry:

```yaml
discovery:
  sources:
    - type: kubernetes
      namespaces: [apps]
      denied_namespaces: [kube-system, default]   # was discovery.security.*
```

The old location still works and is applied when the new one is absent, with a
`discovery_namespace_policy_deprecated_location` warning at startup. It will be
removed in a later release. Moving a security setting silently is the one
migration that must not happen quietly: a deployment that denied `kube-system`
must not start accepting it because a key changed address.

Defaults are unchanged -- `kube-system` and `default` are still denied when
nothing is configured.

A source can now declare its own rules through `DiscoverySource.policy_violation`,
which is optional: a source that does not implement it raises no objection, so
existing third-party sources keep working untouched.

## Discovery: an unknown `source_type` now refuses to start

A discovery source configured with a type nothing provides used to be skipped
with a warning, and the gateway carried on with that source absent. A typo in
`type:` therefore produced a running gateway with no discovery and one line in
the log. It now raises at startup, the way an unknown `event_store.driver`
already did.

**Who is affected:** deployments whose configuration names a source type that
is not installed. They were already not getting that source; now they are told.

**What to do:** fix the type, or install the package that provides it. The
error lists the types that are registered.

A missing *optional dependency* is unchanged -- `ImportError` still degrades
with a warning, because that is a deployment shape rather than a mistake in the
configuration.

Third-party sources now register under the `mcp_hangar.discovery_sources` entry
point group, so adding one no longer means patching the core.

## Removed: `EventBus.on_error`

The hook that registered a callback for exceptions raised inside event handlers
is gone, along with the list it appended to.

**Who is affected:** only code that calls `EventBus.on_error(...)`. Nothing in
Hangar ever did, so the loop that invoked those callbacks ran zero times on
every handler failure -- dead code in the one path that only executes when
something is already wrong. `IEventBus`, the port the application layer depends
on, never declared it.

**What to do:** nothing, unless you registered a callback. If you did, the
information it carried is now a metric: a handler that raises increments
`mcp_hangar_errors{component="event_handler"}`, labelled with the exception
type, and the `event_handler_error` log line now names the failing handler.

The fault barrier itself is unchanged -- one failing handler still does not stop
the others, and `publish()` still does not raise.

## Removed: eight domain event classes that nothing emitted

`CatalogItemApproved`, `CatalogItemDeprecated`, `CatalogItemPublished`,
`CatalogItemRejected`, `ToolSchemaChanged`, `ToolSchemaDriftDetected`,
`BehavioralModeChanged` and `CapabilityDeclarationMissing` are gone from
`mcp_hangar.domain.events`.

**Who is affected:** only code that imports one of those names. No deployment
can have received one of these events, because nothing in Hangar has ever
constructed one -- they were vocabulary for features that were never built, and
an audit found them with no producer and no consumer anywhere in the tree.

**What to do:** delete the import and any handler registered against it. Such a
handler has never been called, so removing it changes no behaviour.

A stream cannot contain one either, so no event store needs migrating. The
remaining unemitted events -- the five discovery ones, four quarantine ones and
`PolicyPushRejected` -- are deliberately kept: those features are live or
planned, and the missing emitter is tracked rather than papered over.

## 2.3.0 — two things to check before you roll out

Neither affects a default deployment. The first matters only if you import the
concrete launchers from the domain layer; the second only if you set
`auth.storage.driver: event_sourcing`.

> The work below was written against a planned 2.2.2. That release was never
> cut -- it became 2.3.0 when the launcher removal landed, so everything here
> ships in 2.3.0.

### The deprecated launcher import paths are gone

Only affects code that imports the concrete launcher classes from the domain
layer. If you import them from `mcp_hangar.infrastructure.launchers`, which is
where they live and what the deprecation warning has been telling you since
**v1.0.2**, nothing changes.

Two import paths were removed:

```python
# Both of these now raise.
from mcp_hangar.domain.services.mcp_server_launcher import DockerLauncher
from mcp_hangar.domain.services import DockerLauncher

# This is the one to use, and always was:
from mcp_hangar.infrastructure.launchers import DockerLauncher
```

The same applies to `SubprocessLauncher`, `ContainerLauncher`, `HttpLauncher`,
`ContainerConfig`, `McpServerLauncher` and `get_launcher`.

`mcp_hangar.domain.services` still exports the launcher **port**,
`IMcpServerLauncher`, along with `LaunchResult` and `TransportClient`. It is the
concrete implementations that moved out — a domain package re-exporting
infrastructure classes is what the deprecation was about.

The shim emitted a `DeprecationWarning` on import from v1.0.2 onward, so a run
of your test suite with warnings visible will list every call site:

```bash
python -W error::DeprecationWarning -m pytest
```

Removing it also broke a real import cycle: the domain reaching for the
concrete launchers is what forced two sagas to import their saga manager inside
a function body rather than at module level.

### If you run `auth.storage.driver: event_sourcing`, read this before upgrading

On that driver, API keys and role assignments were written to the event store
correctly and could not be read back: the writer accepts any domain event, the
reader looked the class up in a hand-maintained table that listed 30 of the 116
event types, and all five the auth aggregates emit were missing. Every API key
stopped authenticating across a restart, and role assignments were invisible
after one. Affected from **1.2.2** (when the driver landed) through **2.2.1**;
`memory` is the default and `sqlite`/`postgresql` were never affected.

**Nothing was lost** — only the read path failed — which is exactly why this
needs planning rather than celebration:

> Credentials and role assignments you believed were gone start working again
> the moment you upgrade, including any `admin` assignment made in that window.

Revocations are events too and replay in order, so anything you revoked stays
revoked. Look at what is dormant before you roll out, and revoke what you do not
want live. The canonical guide has the two `sqlite3` commands for that:
<https://mcp-hangar.io/docs/upgrade/>.

Also in this release, and needing no configuration change: events written before
the `provider` -> `mcp_server` rename (stores from 1.0.1 or earlier) reach their
handlers again instead of replaying into nothing, and a `datetime` field on a
persisted event comes back as a `datetime` rather than a string.

---

## 2.2.0 — action required before you roll out

2.2.0 is a security release. It is a minor rather than a patch because it
changes behaviour that working deployments rely on. Three things can
break a working deployment, and two of them fail silently:

1. **Operator API key.** `POST`/`DELETE /api/mcp_servers/{id}/l7_policy` now
   requires `policy:write` instead of `mcp_servers:write`. A `developer` token
   stops delivering compiled egress policy — the CRD still reports `Compiled`
   and nothing reaches the enforcement point. Move operator keys to
   `provider-admin`, which gained `mcp_servers:read` + `policy:write` for
   exactly this.
2. **OPA policies.** A non-boolean verdict was treated as *allow* (including a
   policy returning the string `"deny"`). It is now a denial. A policy that
   returns an object or a string flips from allowing everything to denying
   everything.
3. **`tool_access.mode`.** A misspelled value used to fall back to `egress`
   with a warning; the server now refuses to start. An absent key still means
   `egress`.

Also changed: REST authorization is enforced on every route (`/config`,
`/discovery`, `/groups`, `/sessions`, `/tools`, `/approvals` reads and the whole
`/auth` subtree previously made no authorization decision at all);
`POST /api/config/reload` no longer accepts a caller-supplied `config_path`;
approvals pending across the upgrade are refused and must be re-requested.

The full guide, with the role-compatibility table and the per-item rationale,
is the canonical one: <https://mcp-hangar.io/docs/upgrade/>.

---

## Upgrading to MCP Hangar v1.0

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
compliance export, Langfuse integration) moved from the core package to the
`enterprise/` directory. As of v1.3.0, the `enterprise/` directory was absorbed
back into `src/mcp_hangar/` and the entire codebase is licensed under MIT.

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

- **All users:** No license key or tier distinction applies. All features
  (provider lifecycle, health checks, circuit breaker, groups, load balancing,
  failover, Prometheus metrics, OTEL export, CLI, hot-reload, batch invocations,
  auth, RBAC, behavioral profiling, compliance export, Langfuse integration) are
  unconditionally available under MIT.

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

Review your `values.yaml` for these additions in the mcp-hangar chart:

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

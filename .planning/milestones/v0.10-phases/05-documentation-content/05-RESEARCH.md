# Phase 5: Documentation Content - Research

**Researched:** 2026-02-28
**Domain:** Technical documentation (MkDocs-material, Python, YAML config reference)
**Confidence:** HIGH

## Summary

Phase 5 creates 4 documentation pages covering the full public surface of MCP Hangar: Configuration Reference, MCP Tools Reference, Provider Groups Guide, and Facade API Guide. All content is derived from verified source code inspection -- no external library research was required since the documentation describes the project's own API surface.

The codebase provides comprehensive docstrings, structured tool signatures, and annotated example configs that serve as authoritative source material. The existing documentation follows consistent patterns (reference pages: parameter tables + code blocks; guide pages: overview + API ref + examples + limits) that the new pages must match.

**Primary recommendation:** Extract documentation content directly from source code (tool docstrings, facade method signatures, config loading code, value object validation ranges). Follow the established style of existing reference and guide pages.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

- **Tools Reference organization:** Group 22 tools by category (6 groups): Lifecycle, Health, Discovery, Groups, Batch, Continuation. Categories match source file organization in server/tools/. Single page with quick-reference summary table at the top listing all 22 tools with one-line descriptions and anchor links. Each tool documented as a structured card: description, parameters table (name/type/default/description), returns table, side effects list, one JSON request/response example.

- **Configuration Reference structure:** Single page covering all 13 YAML sections + environment variables. Each config key documented in a table with columns: key, type, default, validation range, description, notes (for gotchas). One YAML snippet per config section showing common options with inline comments. Environment variables in a dedicated section at the end with its own table (variable, default, description). Follows existing reference page style (hot-reload.md, cli.md).

- **Provider Groups Guide structure:** Concept overview + reference sections (not step-by-step tutorial). Overview section explaining what groups are and when to use them. Reference sections for: load balancing strategies, health policies, circuit breaker, tool access filtering. One YAML config example per load balancing strategy (5 strategies: round_robin, weighted_round_robin, least_connections, random, priority). Follows existing guide structure (BATCH_INVOCATIONS.md pattern: overview, API ref, examples, limits).

- **Facade API Guide structure:** Quick start (minimal working example) + API reference for each method + advanced patterns. Both async (Hangar) and sync (SyncHangar) versions shown side by side in code examples. Framework integration section covers FastAPI only (lifespan events, dependency injection). HangarConfig builder documented with fluent API examples.

- **General documentation conventions:** All 4 pages are single-page documents. Direct, technical tone. Heavy use of markdown tables for parameters and options. Code blocks: YAML for config, Python for API usage, JSON for tool responses. Reference pages integrate under mkdocs.yml "Reference" nav section. Guide pages integrate under mkdocs.yml "Guides" nav section.

### Claude's Discretion

- Exact ordering of sections within each page
- Admonition usage (info, warning boxes) placement
- Cross-reference links between the 4 new pages
- Whether to include a "Troubleshooting" or "Common Mistakes" subsection per page

### Deferred Ideas (OUT OF SCOPE)

None -- discussion stayed within phase scope.

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DOC-01 | Configuration Reference page documents full YAML schema with all keys, defaults, and validation rules | Complete YAML schema extracted from config.py, config.yaml.example, config.max.yaml. All 13 sections identified with defaults and validation ranges from value objects. |
| DOC-02 | Configuration Reference page lists all environment variables with descriptions and examples | 28+ environment variables identified from grep of MCP_and HANGAR_ prefixes across bootstrap/runtime.py, serve.py, observability.py, container.py, auth_config.py |
| DOC-03 | MCP Tools Reference page documents all 22 tools with parameters, return formats, and error codes | All 22 tools read from 7 source files with complete parameter signatures, return schemas, and error conditions extracted from docstrings |
| DOC-04 | MCP Tools Reference page documents side effects and state changes for each tool | Side effects documented in every tool docstring. Categorized as read-only (11 tools) vs. state-changing (11 tools) |
| DOC-05 | Provider Groups Guide covers all 5 load balancing strategies with usage examples | All 5 strategies verified from load_balancer.py with implementation details: round_robin, weighted_round_robin, least_connections, random, priority |
| DOC-06 | Provider Groups Guide covers health policies, circuit breaker, and tool access filtering | Health thresholds from provider_group.py (unhealthy_threshold, healthy_threshold), CircuitBreaker from circuit_breaker.py (failure_threshold, reset_timeout_s, states), ToolsConfig from provider_config.py (allow_list/deny_list at provider/group/member level) |
| DOC-07 | Facade API Guide documents Hangar/SyncHangar public API with method signatures | Complete API extracted from facade.py: Hangar (8 methods), SyncHangar (8 methods), ProviderInfo (6 fields, 2 properties), HealthSummary (3 fields, 2 properties) |
| DOC-08 | Facade API Guide covers HangarConfig builder and framework integration patterns | HangarConfig builder methods (add_provider, enable_discovery, max_concurrency, set_intervals, build, to_dict) and data classes (HangarConfigData, DiscoverySpec) fully documented from source |

</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| mkdocs-material | (existing) | Documentation site theme | Already configured in mkdocs.yml |
| pymdownx extensions | (existing) | Code highlighting, tabs, superfences, details | Already configured in mkdocs.yml |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| admonition extension | (existing) | Info/warning/danger boxes | Already enabled, use for gotchas and important notes |
| pymdownx.tabbed | (existing) | Side-by-side code examples | Already enabled, use for async/sync Facade API examples |

No new dependencies needed -- this phase only creates markdown content files.

## Architecture Patterns

### Recommended Project Structure

New files to create and their locations:

```
docs/
├── reference/
│   ├── configuration.md   # NEW: Configuration Reference (DOC-01, DOC-02)
│   └── tools.md           # NEW: MCP Tools Reference (DOC-03, DOC-04)
└── guides/
    ├── PROVIDER_GROUPS.md  # NEW: Provider Groups Guide (DOC-05, DOC-06)
    └── FACADE_API.md       # NEW: Facade API Guide (DOC-07, DOC-08)
```

mkdocs.yml nav updates:

```yaml
nav:
  # ...existing...
  - Guides:
      # ...existing guides...
      - Provider Groups: guides/PROVIDER_GROUPS.md    # ADD
      - Facade API: guides/FACADE_API.md              # ADD
  - Reference:
      - CLI: reference/cli.md
      - Configuration: reference/configuration.md      # ADD
      - MCP Tools: reference/tools.md                  # ADD
      - Hot-Reload: reference/hot-reload.md            # ADD (currently missing from nav!)
      # ...existing...
```

### Pattern: Reference Page Structure

Based on existing `reference/cli.md` (529 lines) and `reference/hot-reload.md` (337 lines):

- Title with brief one-sentence description
- Sections with `##` headings
- Parameter tables with columns: Option | Type | Default | Description
- Code blocks for examples (YAML/Python/JSON)
- Minimal prose between tables
- Admonitions (`!!! note`, `!!! warning`) for gotchas

### Pattern: Guide Page Structure

Based on existing `guides/BATCH_INVOCATIONS.md`:

- Overview section: what it is, when to use it
- API Reference section: methods/options with tables
- Examples section: complete, runnable code blocks
- Limits / Configuration section: constants, ranges, defaults
- Direct technical tone, no tutorial hand-holding

### Anti-Patterns to Avoid

- **Duplicating docstrings verbatim:** Docstrings contain CHOOSE THIS/SKIP THIS guidance meant for LLM tool selection, not human readers. Rewrite for human audience.
- **Incomplete parameter tables:** Every parameter must have type, default, and description. No "see source" references.
- **Missing validation ranges:** Value objects define strict ranges (e.g., IdleTTL 1-86400). Document these explicitly.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Code highlighting | Custom syntax highlighting | pymdownx.highlight (already configured) | Markdown fenced code blocks handle YAML/Python/JSON automatically |
| Tab groups | Custom HTML | pymdownx.tabbed with `alternate_style: true` | Existing configuration supports `=== "Tab Name"` syntax |
| Anchored headings | Manual anchor links | toc extension with `permalink: true` | Already configured, auto-generates anchors for all headings |

## Common Pitfalls

### Pitfall 1: Tool Categories Don't Match Source Files

**What goes wrong:** The CONTEXT.md says "6 groups" but the tool source code is organized in 7 files. The Lifecycle and Load categories need to be combined.
**Why it happens:** `server/tools/hangar.py` contains both core lifecycle tools (list, start, stop, status) and hot-loading tools (load, unload, reload_config). The `register_hangar_tools` and `register_load_tools` are separate registration functions but live in the same file.
**How to avoid:** Use these 7 categories matching the actual tool organization: Lifecycle (hangar_list, hangar_start, hangar_stop, hangar_status, hangar_reload_config), Hot-Loading (hangar_load, hangar_unload), Provider (hangar_tools, hangar_details, hangar_warm), Health (hangar_health, hangar_metrics), Discovery (hangar_discover, hangar_discovered, hangar_quarantine, hangar_approve, hangar_sources), Groups (hangar_group_list, hangar_group_rebalance), Batch & Continuation (hangar_call, hangar_fetch_continuation, hangar_delete_continuation).
**Warning signs:** CONTEXT.md says "6 groups" but actual tool files organize into 7 logical categories. Use source code as authority.

### Pitfall 2: Inconsistent Config Key Nesting

**What goes wrong:** Some config keys appear at different nesting levels with different meanings.
**Why it happens:** The `tools` key can be either a list (predefined tool schemas) or a dict (access policy with allow_list/deny_list) depending on context. The `command` key appears in both provider-level (subprocess command) and inside provider spec (container command override).
**How to avoid:** Document the dual-format `tools` configuration explicitly with separate subsections. Show both list and dict formats with clear labels.
**Warning signs:** Users will try to use list format when they want access policy, or vice versa.

### Pitfall 3: Environment Variable Prefix Confusion

**What goes wrong:** Some environment variables use `MCP_` prefix, others use `HANGAR_` prefix, and some use neither (OTEL_, LANGFUSE_, JAEGER_).
**Why it happens:** Third-party integrations (OpenTelemetry, Langfuse, Jaeger) use their own standard prefixes. Legacy code has `HANGAR_` prefix alternatives.
**How to avoid:** Group env vars by prefix in the documentation. Note that `HANGAR_*` alternatives exist for backward compatibility but `MCP_*` is canonical.

### Pitfall 4: Missing Hot-Reload Reference in Nav

**What goes wrong:** The `reference/hot-reload.md` file exists but is not in the mkdocs.yml nav.
**Why it happens:** It was likely omitted during a previous nav restructure.
**How to avoid:** When updating mkdocs.yml nav for new pages, also add hot-reload.md to the Reference section.

## Code Examples

### MCP Tools Reference -- Tool Card Format

Each of the 22 tools should follow this template:

```markdown
### `hangar_call` {#hangar_call}

Invoke tools on MCP providers (single or batch). Auto-starts cold providers.

**Parameters:**

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `calls` | `list[object]` | required | 1-100 items | List of `{provider, tool, arguments, timeout?}` |
| `max_concurrency` | `int` | `10` | 1-50 | Parallel workers for this batch |
| `timeout` | `float` | `60` | 1-300 | Batch timeout in seconds |
| `fail_fast` | `bool` | `false` | -- | Stop batch on first error |
| `max_attempts` | `int` | `1` | 1-10 | Total attempts per call including retries |

**Side Effects:** May start cold providers. Executes tool calls on providers.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `batch_id` | `str` | Unique batch identifier |
| `success` | `bool` | `true` if all calls succeeded |
| `total` | `int` | Total calls in batch |
| `succeeded` | `int` | Successful call count |
| `failed` | `int` | Failed call count |
| `elapsed_ms` | `float` | Total batch execution time |
| `results` | `list[object]` | Per-call results with `index`, `call_id`, `success`, `result`, `error`, `elapsed_ms` |

**Example:**

` `` json
// Request
{"calls": [{"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]}

// Response
{"batch_id": "abc-123", "success": true, "total": 1, "succeeded": 1, "failed": 0,
 "elapsed_ms": 45.2, "results": [{"index": 0, "success": true, "result": 3}]}
` ``
```

### Configuration Reference -- Config Section Format

```markdown
## `providers`

Provider definitions. Each key is a unique provider ID.

` `` yaml
providers:
  math:
    mode: subprocess
    command: [python, -m, math_server]
    idle_ttl_s: 300
    health_check_interval_s: 60
` ``

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `mode` | `str` | `"subprocess"` | subprocess, docker, remote | Provider mode (`container`/`podman` normalize to `docker`) |
| `command` | `list[str]` | -- | -- | Command for subprocess mode (required for subprocess) |
| `image` | `str` | -- | -- | Docker image for docker mode (required for docker) |
| `endpoint` | `str` | -- | -- | HTTP endpoint for remote mode (required for remote) |
| `idle_ttl_s` | `int` | `300` | 1-86400 | Seconds before auto-shutdown when idle |
| `health_check_interval_s` | `int` | `60` | 5-3600 | Health check interval in seconds |
| `max_consecutive_failures` | `int` | `3` | 1-100 | Failures before marking degraded |
```

### Provider Groups Guide -- Strategy Example

```markdown
### Round Robin

Distributes requests evenly across all healthy members in order.

` `` yaml
providers:
  llm-group:
    mode: group
    strategy: round_robin
    min_healthy: 1
    members:
      - id: llm-1
        mode: subprocess
        command: [python, -m, llm_server]
      - id: llm-2
        mode: subprocess
        command: [python, -m, llm_server]
` ``

Requests cycle through members: llm-1, llm-2, llm-1, llm-2, ...
```

### Facade API Guide -- Async/Sync Side-by-Side

```markdown
=== "Async"

    ` `` python
    from mcp_hangar import Hangar

    async with Hangar.from_config("config.yaml") as hangar:
        result = await hangar.invoke("math", "add", {"a": 1, "b": 2})
        print(result)
    ` ``

=== "Sync"

    ` `` python
    from mcp_hangar import SyncHangar

    with SyncHangar.from_config("config.yaml") as hangar:
        result = hangar.invoke("math", "add", {"a": 1, "b": 2})
        print(result)
    ` ``
```

## Content Data Extracted from Source Code

### Complete MCP Tool Inventory (22 tools, 7 categories)

#### Category 1: Lifecycle (5 tools) -- `server/tools/hangar.py`

**hangar_list**

- Params: `state_filter: str | None = None` (filter: "cold", "ready", "degraded", "dead")
- Returns: `{providers: [{provider, state, mode, alive, tools_count, health_status, tools_predefined, description?}], groups: [{group_id, state, strategy, healthy_count, total_members, ...}], runtime_providers: [{provider, state, source, verified, ephemeral, loaded_at, lifetime_seconds}]}`
- Side effects: None (read-only, CQRS query)
- Errors: None
- Notes: Use for exact data/filtering. Use hangar_status for human-readable dashboard.

**hangar_start**

- Params: `provider: str` (Provider ID or Group ID)
- Returns provider: `{provider, state, tools: list[str]}`
- Returns group: `{group, state, members_started, healthy_count, total_members}`
- Side effects: Starts provider process/container. State COLD -> READY.
- Errors: `ValueError("unknown_provider: <id>")`, `ValueError("unknown_group: <id>")`

**hangar_stop**

- Params: `provider: str` (Provider ID or Group ID)
- Returns provider: `{stopped, reason}`
- Returns group: `{group, state, stopped: true}`
- Side effects: Stops provider process/container. State -> COLD.
- Errors: `ValueError("unknown_provider: <id>")`

**hangar_status**

- Params: None
- Returns: `{providers: [{id, indicator, state, mode, last_used?}], groups: [{id, indicator, state, healthy_members, total_members}], runtime_providers: [{id, indicator, state, source, verified}], summary: {healthy_providers, total_providers, runtime_providers, runtime_healthy, uptime, uptime_seconds}, formatted: str}`
- Indicator values: `[READY]`, `[COLD]`, `[STARTING]`, `[DEGRADED]`, `[DEAD]`
- Side effects: None (read-only)

**hangar_reload_config**

- Params: `graceful: bool = True` (wait for idle before stopping)
- Returns: `{status: "success"|"failed", message, providers_added: [str], providers_removed: [str], providers_updated: [str], providers_unchanged: [str], duration_ms: float}`
- Side effects: Stops/starts providers based on config file changes
- Errors: Returns `{status: "failed", message, error_type}` on failure

#### Category 2: Hot-Loading (2 tools) -- `server/tools/hangar.py`

**hangar_load** (async)

- Params: `name: str` (registry name), `force_unverified: bool = False`, `allow_tools: list[str] | None = None`, `deny_tools: list[str] | None = None`
- Returns success: `{status: "loaded", provider, tools: list[str]}`
- Returns ambiguous: `{status: "ambiguous", message, matches: list[str]}`
- Returns not found: `{status: "not_found", message}`
- Returns missing secrets: `{status: "missing_secrets", provider_name, missing: list[str], instructions}`
- Returns unverified: `{status: "unverified", provider_name, message, instructions}`
- Returns not configured: `{status: "failed", message}`
- Side effects: Downloads and starts provider process. Adds to runtime registry. Ephemeral (lost on restart).
- Notes: Browse registry at https://mcp.so/servers

**hangar_unload**

- Params: `provider: str` (Provider ID from hangar_load result)
- Returns success: `{status: "unloaded", provider, message, lifetime_seconds: float}`
- Returns not hot-loaded: `{status: "not_hot_loaded", provider, message}`
- Returns not configured: `{status: "failed", message}`
- Side effects: Stops provider process. Removes from runtime registry. Only works for hot-loaded providers.

#### Category 3: Provider (3 tools) -- `server/tools/provider.py`

**hangar_tools**

- Params: `provider: str` (Provider ID or Group ID)
- Returns provider: `{provider, state, predefined: bool, tools: [{name, description, inputSchema}]}`
- Returns group: `{provider, group: true, tools: [{name, description, inputSchema}]}`
- Side effects: May start a cold provider to discover tools.
- Errors: `ValueError("unknown_provider: <id>")`, `ValueError("no_healthy_members_in_group: <id>")`
- Notes: Tool access filtering applied (allow_list/deny_list)

**hangar_details**

- Params: `provider: str` (Provider ID or Group ID)
- Returns provider: `{provider, state, mode, alive, tools: [...], health: {consecutive_failures, last_check, ...}, idle_time, meta, tools_policy: {type, has_allow_list, has_deny_list, filtered_count?}}`
- Returns group: `{group_id, description, state, strategy, min_healthy, healthy_count, total_members, is_available, circuit_open, members: [{id, state, in_rotation, weight, priority, consecutive_failures}]}`
- Side effects: None (read-only)
- Errors: `ValueError("unknown_provider: <id>")`

**hangar_warm**

- Params: `providers: str | None = None` (comma-separated IDs, null = all)
- Returns: `{warmed: [str], already_warm: [str], failed: [{id, error}], summary: str}`
- Side effects: Starts specified provider processes. Groups are skipped.
- Errors: None at tool level; individual failures captured in `failed` list

#### Category 4: Health (2 tools) -- `server/tools/health.py`

**hangar_health**

- Params: None
- Returns: `{status: str, providers: {total, by_state: {cold?, ready?, degraded?, dead?}}, groups: {total, by_state, total_members, healthy_members}, security: {rate_limiting: {active_buckets, config}}}`
- Side effects: None (read-only)

**hangar_metrics**

- Params: `format: str = "json"` ("json" or "prometheus")
- Returns JSON: `{providers: {<id>: {state, mode, tools_count, invocations, errors, avg_latency_ms}}, groups: {<id>: {state, strategy, total_members, healthy_members}}, tool_calls: {<provider.tool>: {count, errors}}, discovery, errors: {<type>: int}, performance, summary: {total_providers, total_groups, total_tool_calls, total_errors}}`
- Returns Prometheus: `{metrics: str}` (Prometheus exposition format)
- Side effects: None (read-only)

#### Category 5: Discovery (5 tools) -- `server/tools/discovery.py`

**hangar_discover** (async)

- Params: None
- Returns success: `{discovered_count, registered_count, updated_count, deregistered_count, quarantined_count, error_count, duration_ms, source_results: {<source_type>: int}}`
- Returns not configured: `{error: "Discovery not configured. Enable discovery in config.yaml"}`
- Side effects: Scans all enabled sources. Updates pending provider list.

**hangar_discovered**

- Params: None
- Returns: `{pending: [{name, source, mode, discovered_at, fingerprint}]}`
- Returns not configured: `{error: str}`
- Side effects: None (read-only)

**hangar_quarantine**

- Params: None
- Returns: `{quarantined: [{name, source, reason, quarantine_time}]}`
- Returns not configured: `{error: str}`
- Side effects: None (read-only)

**hangar_approve** (async)

- Params: `provider: str` (from hangar_discovered or hangar_quarantine output)
- Returns success: `{approved: true, provider, status: "registered"}`
- Returns not found: `{approved: false, provider, error}`
- Returns not configured: `{error: str}`
- Side effects: Registers provider in cold state. Removes from pending/quarantine.

**hangar_sources** (async)

- Params: None
- Returns: `{sources: [{source_type, mode, is_healthy, is_enabled, last_discovery, providers_count, error_message}]}`
- Returns not configured: `{error: str}`
- Side effects: None (read-only)

#### Category 6: Groups (2 tools) -- `server/tools/groups.py`

**hangar_group_list**

- Params: None
- Returns: `{groups: [{group_id, description, state, strategy, min_healthy, healthy_count, total_members, is_available, circuit_open, members: [{id, state, in_rotation, weight, priority, consecutive_failures}]}]}`
- Side effects: None (read-only)

**hangar_group_rebalance**

- Params: `group: str` (Group ID)
- Returns: `{group_id, state, healthy_count, total_members, members_in_rotation: [str]}`
- Side effects: Re-checks all members. Recovered members rejoin rotation, failed removed. Resets circuit breaker.
- Errors: `ValueError("unknown_group: <id>")`

#### Category 7: Batch & Continuation (3 tools) -- `server/tools/batch/__init__.py`, `server/tools/continuation.py`

**hangar_call**

- Params: `calls: list[{provider, tool, arguments, timeout?}]`, `max_concurrency: int = 10` (1-50), `timeout: float = 60` (1-300), `fail_fast: bool = False`, `max_attempts: int = 1` (1-10)
- Returns success: `{batch_id, success: true, total, succeeded, failed, elapsed_ms, results: [{index, call_id, success: true, result, error: null, error_type: null, elapsed_ms}]}`
- Returns partial failure: `{batch_id, success: false, total, succeeded, failed, elapsed_ms, results: [{..., retry_metadata?: {attempts, retries}}]}`
- Returns validation error: `{batch_id, success: false, error: "Validation failed", validation_errors: [{index, field, message}]}`
- Returns truncated: Individual result may contain `{truncated: true, truncated_reason, original_size_bytes, continuation_id}`
- Side effects: May start cold providers. Executes tool calls via ThreadPoolExecutor. Two-level concurrency control (per-batch + system-wide semaphores).
- Constants: MAX_CALLS_PER_BATCH=100, MAX_RESPONSE_SIZE_BYTES=10MB, MAX_TOTAL_RESPONSE_SIZE_BYTES=50MB

**hangar_fetch_continuation**

- Params: `continuation_id: str` (starts with "cont_"), `offset: int = 0`, `limit: int = 500000` (max: 2000000)
- Returns found: `{found: true, data, total_size_bytes, offset, has_more, complete}`
- Returns not found: `{found: false, error: "Continuation not found (may have expired)"}`
- Returns cache unavailable: `{found: false, error: "Truncation cache not available..."}`
- Side effects: None (read-only cache access)
- Errors: `ValueError` if continuation_id empty, doesn't start with "cont_", or offset negative

**hangar_delete_continuation**

- Params: `continuation_id: str` (starts with "cont_")
- Returns success: `{deleted: true, continuation_id}`
- Returns not found: `{deleted: false, continuation_id}`
- Returns cache unavailable: `{deleted: false, continuation_id, error}`
- Side effects: Removes cached response from memory
- Errors: `ValueError` if continuation_id empty

### Complete Configuration Schema (13 YAML sections)

#### 1. `providers` Section

Top-level mapping of provider ID to provider config.

| Key | Type | Default | Validation | Notes |
|-----|------|---------|------------|-------|
| `mode` | str | `"subprocess"` | subprocess/docker/remote/container/podman/group | container/podman normalize to docker |
| `command` | list[str] | -- | -- | Required for subprocess mode |
| `image` | str | -- | -- | Required for docker mode |
| `endpoint` | str | -- | -- | Required for remote mode |
| `env` | dict[str,str] | `{}` | -- | Environment variables for provider process |
| `idle_ttl_s` | int | `300` | 1-86400 | Seconds before auto-shutdown |
| `health_check_interval_s` | int | `60` | 5-3600 | Health check interval |
| `max_consecutive_failures` | int | `3` | 1-100 | Failures before marking degraded |
| `volumes` | list[str] | `[]` | -- | Docker volumes (docker mode only) |
| `build` | dict | -- | -- | Docker build config (docker mode only) |
| `resources` | dict | `{memory: "512m", cpu: "1.0"}` | -- | Container resources (docker mode only) |
| `network` / `network_mode` | str | `"none"` | -- | Container network (docker mode only) |
| `read_only` | bool | `true` | -- | Read-only filesystem (docker mode only) |
| `user` | str | -- | -- | Container user. `"current"` maps to `uid:gid` |
| `args` | list[str] | -- | -- | Container CMD override (docker mode only) |
| `description` | str | -- | -- | Human-readable description |
| `tools` | list / dict | -- | -- | Predefined schemas (list) OR access policy (dict) |
| `auth` | dict | -- | -- | HTTP auth config (remote mode only) |
| `tls` | dict | -- | -- | TLS config (remote mode only) |
| `http` | dict | -- | -- | HTTP transport config (remote mode only) |
| `max_concurrency` | int | -- | -- | Per-provider concurrency limit |

**`tools` dual format:**

- List: `[{name, description, inputSchema}]` -- predefined tool schemas, provider not started to discover
- Dict: `{allow_list: [str], deny_list: [str]}` -- fnmatch glob patterns for tool access filtering

#### 2. `execution` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `max_concurrency` | int | `50` | System-wide concurrency limit (0 = unlimited) |
| `default_provider_concurrency` | int | `10` | Default per-provider concurrency limit |

#### 3. `discovery` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | -- | Enable/disable discovery |
| `refresh_interval_s` | int | -- | Scan interval in seconds |
| `auto_register` | bool | -- | Auto-register discovered providers |
| `sources` | list[dict] | `[]` | Discovery source configurations |
| `security` | dict | -- | Security constraints for discovery |
| `lifecycle` | dict | -- | Lifecycle management for discovered providers |

**`sources[]` entry:**

| Key | Type | Notes |
|-----|------|-------|
| `type` | str | kubernetes/docker/filesystem/entrypoint |
| `mode` | str | additive/authoritative |
| `path` / `pattern` | str | For filesystem source |
| `watch` | bool | For filesystem source |
| `namespaces` | list[str] | For kubernetes source |
| `label_selector` | str | For kubernetes source |
| `in_cluster` | bool | For kubernetes source |
| `group` | str | Target group for discovered providers |

**`security` sub-section:**

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `allowed_namespaces` | list[str] | -- | Kubernetes namespace allowlist |
| `denied_namespaces` | list[str] | -- | Kubernetes namespace denylist |
| `require_health_check` | bool | -- | Require health check before registration |
| `require_mcp_schema` | bool | -- | Require MCP schema |
| `max_providers_per_source` | int | -- | Limit per source |
| `max_registration_rate` | int | -- | Rate limit |
| `health_check_timeout_s` | float | -- | Health check timeout |
| `quarantine_on_failure` | bool | -- | Quarantine on health check failure |

**`lifecycle` sub-section:**

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `default_ttl_s` | int | -- | Default TTL for discovered providers |
| `check_interval_s` | int | -- | Lifecycle check interval |
| `drain_timeout_s` | int | -- | Drain timeout before removal |

#### 4. `retry` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `default_policy.max_attempts` | int | -- | Maximum retry attempts |
| `default_policy.backoff` | str | -- | Backoff strategy |
| `default_policy.initial_delay` | float | -- | Initial delay in seconds |
| `default_policy.max_delay` | float | -- | Maximum delay in seconds |
| `default_policy.retry_on` | list[str] | -- | Exception types to retry on |

#### 5. `event_store` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | -- | Enable event persistence |
| `driver` | str | -- | `sqlite` or `memory` |
| `path` | str | -- | SQLite database path (sqlite driver only) |

#### 6. `knowledge_base` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | -- | Enable knowledge base |
| `dsn` | str | -- | Database connection string |
| `pool_size` | int | -- | Connection pool size |
| `cache_ttl_s` | int | -- | Cache TTL in seconds |

#### 7. `logging` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `level` | str | `"INFO"` | Log level (DEBUG/INFO/WARNING/ERROR) |
| `json_format` | bool | `false` | Enable structured JSON logging |
| `file` | str | -- | Log file path |

#### 8. `health` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | -- | Enable health checks |
| `interval_s` | int | -- | Global health check interval |

#### 9. `observability` Section

**`tracing` sub-section:**

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | -- | Enable OpenTelemetry tracing |
| `otlp_endpoint` | str | `"http://localhost:4317"` | OTLP exporter endpoint |
| `service_name` | str | `"mcp-hangar"` | Service name for traces |
| `jaeger_host` | str | -- | Jaeger host |
| `jaeger_port` | int | `6831` | Jaeger port |
| `console_export` | bool | -- | Export traces to console |

**`langfuse` sub-section:**

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | `false` | Enable Langfuse integration |
| `public_key` | str | -- | Langfuse public key |
| `secret_key` | str | -- | Langfuse secret key (use `${LANGFUSE_SECRET_KEY}`) |
| `host` | str | `"https://cloud.langfuse.com"` | Langfuse host |
| `sample_rate` | float | `1.0` | Trace sample rate (0.0-1.0) |
| `scrub_inputs` | bool | `false` | Scrub sensitive inputs |
| `scrub_outputs` | bool | `false` | Scrub sensitive outputs |

#### 10. `auth` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | -- | Enable authentication |
| `allow_anonymous` | bool | -- | Allow unauthenticated requests |
| `api_key.enabled` | bool | -- | Enable API key auth |
| `api_key.header_name` | str | -- | Header name for API key |
| `oidc.enabled` | bool | -- | Enable OIDC auth |
| `oidc.issuer` | str | -- | OIDC issuer URL |
| `oidc.audience` | str | -- | Expected audience |
| `oidc.subject_claim` | str | -- | Subject claim field |
| `oidc.groups_claim` | str | -- | Groups claim field |
| `oidc.email_claim` | str | -- | Email claim field |
| `oidc.tenant_claim` | str | -- | Tenant claim field |
| `opa.enabled` | bool | -- | Enable OPA policy check |
| `opa.url` | str | -- | OPA server URL |
| `opa.policy_path` | str | -- | OPA policy path |
| `opa.timeout` | float | -- | OPA request timeout |
| `storage` | dict | -- | Auth storage config (driver, path, host, etc.) |
| `rate_limit` | dict | -- | Auth rate limit config |
| `role_assignments` | list[dict] | -- | Role assignment list |

#### 11. `config_reload` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | -- | Enable hot config reload |
| `use_watchdog` | bool | -- | Use file system watcher |
| `interval_s` | int | -- | Polling interval |

#### 12. `batch` Section

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `max_calls` | int | `100` | Max calls per batch |
| `max_concurrency` | int | `50` | Max batch concurrency |
| `default_timeout` | float | `60` | Default batch timeout |
| `max_timeout` | float | `300` | Maximum allowed timeout |
| `max_response_size_bytes` | int | `10485760` (10MB) | Max single response size |
| `max_total_response_size_bytes` | int | `52428800` (50MB) | Max total batch response size |

#### 13. Group Configuration (within `providers` section, mode="group")

| Key | Type | Default | Validation | Notes |
|-----|------|---------|------------|-------|
| `mode` | str | -- | `"group"` | Must be "group" |
| `strategy` | str | `"round_robin"` | round_robin/weighted_round_robin/least_connections/random/priority | Load balancing strategy |
| `min_healthy` | int | `1` | >= 1 | Minimum healthy members for HEALTHY state |
| `auto_start` | bool | `true` | -- | Auto-start members on add |
| `description` | str | -- | -- | Group description |
| `health.unhealthy_threshold` | int | `2` | >= 1 | Consecutive failures before removal |
| `health.healthy_threshold` | int | `1` | >= 1 | Consecutive successes before re-add |
| `circuit_breaker.failure_threshold` | int | `10` | >= 1 | Failures before circuit opens |
| `circuit_breaker.reset_timeout_s` | float | `60.0` | >= 1.0 | Seconds before circuit auto-resets |
| `tools` | dict | -- | -- | Group-level tool access policy `{allow_list, deny_list}` |
| `members` | list[dict] | `[]` | -- | Member provider configs with `id`, `weight` (1-100), `priority` (1-100) |

### Complete Environment Variables

#### Server / CLI

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONFIG` | `"config.yaml"` | Path to YAML configuration file |
| `MCP_MODE` | `"stdio"` | Server mode: `stdio` or `http` |
| `MCP_HTTP_HOST` | `"0.0.0.0"` | HTTP bind host |
| `MCP_HTTP_PORT` | `8000` | HTTP bind port |
| `MCP_LOG_LEVEL` | `"INFO"` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `MCP_JSON_LOGS` | `"false"` | Enable structured JSON logging |

#### Security / Runtime

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_RATE_LIMIT_RPS` | `"10"` | Rate limit requests per second |
| `MCP_RATE_LIMIT_BURST` | `"20"` | Rate limit burst size |
| `MCP_ALLOW_ABSOLUTE_PATHS` | `"false"` | Allow absolute paths in input validation |

#### Persistence

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_PERSISTENCE_ENABLED` | `"false"` | Enable state persistence |
| `MCP_DATABASE_PATH` | `"data/mcp_hangar.db"` | SQLite database path |
| `MCP_DATABASE_WAL` | `"true"` | Enable WAL mode for SQLite |
| `MCP_AUTO_RECOVER` | `"true"` | Auto-recover persisted state on startup |

#### Observability / Tracing

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRACING_ENABLED` | `"true"` | Enable OpenTelemetry tracing |
| `MCP_TRACING_CONSOLE` | from config | Enable console trace export |
| `MCP_ENVIRONMENT` | `"development"` | Deployment environment label |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `"http://localhost:4317"` | OTLP exporter endpoint |
| `OTEL_SERVICE_NAME` | `"mcp-hangar"` | OpenTelemetry service name |
| `JAEGER_HOST` | -- | Jaeger agent host |
| `JAEGER_PORT` | `6831` | Jaeger agent port |

#### Langfuse

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_LANGFUSE_ENABLED` | `"false"` | Enable Langfuse LLM observability |
| `LANGFUSE_PUBLIC_KEY` | -- | Langfuse public API key |
| `LANGFUSE_SECRET_KEY` | -- | Langfuse secret key (sensitive) |
| `LANGFUSE_HOST` | `"https://cloud.langfuse.com"` | Langfuse API host |
| `MCP_LANGFUSE_SAMPLE_RATE` | `"1.0"` | Trace sampling rate (0.0-1.0) |
| `MCP_LANGFUSE_SCRUB_INPUTS` | `"false"` | Redact sensitive tool inputs |
| `MCP_LANGFUSE_SCRUB_OUTPUTS` | `"false"` | Redact sensitive tool outputs |

**Legacy alternatives (backward compat):**

| Variable | Maps To |
|----------|---------|
| `HANGAR_LANGFUSE_ENABLED` | `MCP_LANGFUSE_ENABLED` |
| `HANGAR_LANGFUSE_SAMPLE_RATE` | `MCP_LANGFUSE_SAMPLE_RATE` |
| `HANGAR_LANGFUSE_SCRUB_INPUTS` | `MCP_LANGFUSE_SCRUB_INPUTS` |
| `HANGAR_LANGFUSE_SCRUB_OUTPUTS` | `MCP_LANGFUSE_SCRUB_OUTPUTS` |

#### Container Runtime

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONTAINER_RUNTIME` | -- | Force container runtime (docker/podman) |
| `MCP_CI_RELAX_VOLUME_PERMS` | -- | Relax volume permission checks in CI |
| `MCP_CONTAINER_INHERIT_STDERR` | -- | Inherit stderr from container processes |

#### Auth

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_JWT_MAX_TOKEN_LIFETIME` | -- | Maximum JWT token lifetime |

### Facade API Complete Reference

#### HangarConfig Builder

| Method | Params | Returns | Description |
|--------|--------|---------|-------------|
| `HangarConfig()` | -- | HangarConfig | Create empty config builder |
| `.add_provider(name, *, mode, command, image, url, env, idle_ttl_s)` | name: str, mode: str="subprocess", command: list[str]\|None, image: str\|None, url: str\|None, env: dict\|None, idle_ttl_s: int=300 | self | Add provider. Validates mode-specific requirements. |
| `.enable_discovery(*, docker, kubernetes, filesystem)` | docker: bool=False, kubernetes: bool=False, filesystem: list[str]\|None=None | self | Enable discovery sources |
| `.max_concurrency(value)` | value: int (1-100) | self | Set thread pool size for invoke(). Default: 20. |
| `.set_intervals(*, gc_interval_s, health_check_interval_s)` | gc_interval_s: int\|None, health_check_interval_s: int\|None | self | Set background worker intervals (defaults: 30, 10) |
| `.build()` | -- | HangarConfigData | Build and validate. Cannot modify after build(). |
| `.to_dict()` | -- | dict | Convert to YAML-compatible dict format |

**Raises:** `ConfigurationError` if provider name empty, mode invalid, mode-specific params missing, or already built.

#### Hangar (async)

| Method | Params | Returns | Description |
|--------|--------|---------|-------------|
| `Hangar(config, *, config_path)` | config: HangarConfigData\|None, config_path: str\|Path\|None | -- | Constructor |
| `Hangar.from_config(config_path)` | config_path: str\|Path | Hangar | Create from YAML file |
| `Hangar.from_builder(config)` | config: HangarConfigData | Hangar | Create from builder |
| `await .start()` | -- | None | Bootstrap and start. Auto-called by `async with`. |
| `await .stop()` | -- | None | Stop all providers and workers. Auto-called by `async with`. |
| `await .invoke(provider_name, tool_name, arguments, *, timeout_s)` | provider_name: str, tool_name: str, arguments: dict\|None=None, timeout_s: float=30.0 | Any | Invoke tool. Auto-starts cold providers. |
| `await .start_provider(name)` | name: str | None | Explicitly start a provider |
| `await .stop_provider(name)` | name: str | None | Stop a provider |
| `await .get_provider(name)` | name: str | ProviderInfo | Get provider state snapshot |
| `await .list_providers()` | -- | list[ProviderInfo] | List all providers |
| `await .health()` | -- | HealthSummary | Health summary for all providers |
| `await .health_check(name)` | name: str | bool | Run health check on specific provider |

**Raises:** `ConfigurationError` (not started), `ProviderNotFoundError`, `ToolNotFoundError`, `ToolInvocationError`, `TimeoutError`

#### SyncHangar

Same methods as Hangar but synchronous. Uses `with` instead of `async with`.

| Method | Params | Returns |
|--------|--------|---------|
| `SyncHangar(hangar)` | hangar: Hangar | -- |
| `SyncHangar.from_config(config_path)` | config_path: str\|Path | SyncHangar |
| `SyncHangar.from_builder(config)` | config: HangarConfigData | SyncHangar |
| `.start()` | -- | None |
| `.stop()` | -- | None |
| `.invoke(provider_name, tool_name, arguments, *, timeout_s)` | same as Hangar | Any |
| `.start_provider(name)` | name: str | None |
| `.stop_provider(name)` | name: str | None |
| `.get_provider(name)` | name: str | ProviderInfo |
| `.list_providers()` | -- | list[ProviderInfo] |
| `.health()` | -- | HealthSummary |
| `.health_check(name)` | name: str | bool |

#### Data Classes

**ProviderInfo** (frozen dataclass):

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Provider name |
| `state` | str | Current state (cold/ready/degraded/dead) |
| `mode` | str | Provider mode (subprocess/docker/remote) |
| `tools` | list[str] | Tool names |
| `last_used` | float\|None | Last used timestamp |
| `error` | str\|None | Error message if any |
| `is_ready` | property -> bool | `state == "ready"` |
| `is_cold` | property -> bool | `state == "cold"` |

**HealthSummary** (frozen dataclass):

| Field | Type | Description |
|-------|------|-------------|
| `providers` | dict[str, str] | name -> state mapping |
| `ready_count` | int | Count of ready providers |
| `total_count` | int | Total provider count |
| `all_ready` | property -> bool | All providers ready |
| `any_ready` | property -> bool | At least one ready |

**HangarConfigData** (dataclass):

| Field | Type | Default |
|-------|------|---------|
| `providers` | dict[str, dict] | `{}` |
| `discovery` | DiscoverySpec | default DiscoverySpec |
| `gc_interval_s` | int | `30` |
| `health_check_interval_s` | int | `10` |
| `max_concurrency` | int | `20` |

**DiscoverySpec** (dataclass):

| Field | Type | Default |
|-------|------|---------|
| `docker` | bool | `False` |
| `kubernetes` | bool | `False` |
| `filesystem` | list[str] | `[]` |

### Provider Groups Domain Model

#### Load Balancing Strategies

| Strategy | Enum Value | Behavior | Uses Weight | Uses Priority |
|----------|------------|----------|-------------|---------------|
| Round Robin | `round_robin` | Sequential cycling through healthy members | No | No |
| Weighted Round Robin | `weighted_round_robin` | Nginx smooth weighted algorithm. Higher weight = more requests | Yes (1-100) | No |
| Least Connections | `least_connections` | Selects member with oldest `last_selected_at` timestamp | No | No |
| Random | `random` | Weighted random selection from healthy members | Yes (as probability weight) | No |
| Priority | `priority` | Selects lowest priority number. Primary/backup pattern | No | Yes (1-100, lower = higher priority) |

#### Group States

| State | Enum Value | Condition | Can Accept Requests |
|-------|------------|-----------|---------------------|
| INACTIVE | `inactive` | 0 healthy members | No |
| PARTIAL | `partial` | healthy < min_healthy | Yes (if circuit closed and healthy >= 1) |
| HEALTHY | `healthy` | healthy >= min_healthy | Yes |
| DEGRADED | `degraded` | Circuit breaker is open | No |

#### Health Policy

- `unhealthy_threshold` (default: 2): Consecutive failures before member removed from rotation
- `healthy_threshold` (default: 1): Consecutive successes before member added back to rotation
- Members re-enter rotation only when provider state is READY AND consecutive_successes >= healthy_threshold

#### Circuit Breaker

- States: CLOSED -> OPEN -> (auto-reset after timeout) -> CLOSED
- `failure_threshold` (default: 10): Total group failures before circuit opens
- `reset_timeout_s` (default: 60.0): Seconds before auto-recovery attempt
- When open: All requests rejected (select_member returns None)
- When group rebalances: Circuit resets
- When reset timeout elapses: Next `allow_request()` call closes circuit

#### Tool Access Filtering

Three-level policy hierarchy:

1. **Provider-level:** `tools: {allow_list: ["pattern*"], deny_list: ["internal_*"]}`
2. **Group-level:** Same format, applied to group config
3. **Member-level:** Same format, inside member config

Pattern matching uses fnmatch glob patterns (`*`, `?`, `[seq]`).
Resolution: allow_list takes precedence if set; deny_list only if allow_list empty; empty lists = all tools visible.

### mkdocs.yml Navigation Updates Required

Current Reference section:

```yaml
- Reference:
    - CLI: reference/cli.md
    - Changelog: changelog.md
    - Security: security.md
    - Auth Security Audit: security/AUTH_SECURITY_AUDIT.md
    - Code of Conduct: code-of-conduct.md
```

Required additions:

```yaml
- Reference:
    - CLI: reference/cli.md
    - Configuration: reference/configuration.md       # NEW
    - MCP Tools: reference/tools.md                   # NEW
    - Hot-Reload: reference/hot-reload.md             # RESTORE (file exists but not in nav)
    - Changelog: changelog.md
    - Security: security.md
    - Auth Security Audit: security/AUTH_SECURITY_AUDIT.md
    - Code of Conduct: code-of-conduct.md
```

Current Guides section:

```yaml
- Guides:
    - Testing: guides/TESTING.md
    - Containers: guides/CONTAINERS.md
    - Discovery: guides/DISCOVERY.md
    - HTTP Transport: guides/HTTP_TRANSPORT.md
    - Batch Invocations: guides/BATCH_INVOCATIONS.md
    - Observability: guides/OBSERVABILITY.md
    - Authentication: guides/AUTHENTICATION.md
    - Kubernetes: guides/KUBERNETES.md
```

Required additions:

```yaml
- Guides:
    # ...existing...
    - Provider Groups: guides/PROVIDER_GROUPS.md      # NEW
    - Facade API: guides/FACADE_API.md                # NEW
```

## Open Questions

1. **Tool category count discrepancy**
   - What we know: CONTEXT.md says "6 groups" but there are 7 logical categories (Lifecycle + Hot-Loading are separate in source but could be merged)
   - What's unclear: Whether to merge Lifecycle + Hot-Loading into one category or keep separate
   - Recommendation: Use 7 categories matching source organization. The "6 groups" in CONTEXT.md was approximate. The summary table will make navigation clear regardless.

2. **Missing hot-reload.md from nav**
   - What we know: `docs/reference/hot-reload.md` exists (337 lines) but is absent from mkdocs.yml nav
   - What's unclear: Whether this was intentional
   - Recommendation: Restore it to nav when adding new reference pages. This is a minor fix.

3. **Environment variable precedence**
   - What we know: Some settings can be configured via both YAML config and env vars (e.g., log level)
   - What's unclear: Exact precedence rules for all settings
   - Recommendation: Document that env vars override YAML config where both exist, consistent with standard 12-factor practice.

## Sources

### Primary (HIGH confidence)

- `packages/core/mcp_hangar/server/tools/hangar.py` -- 7 lifecycle/hot-loading tool implementations
- `packages/core/mcp_hangar/server/tools/provider.py` -- 3 provider tool implementations
- `packages/core/mcp_hangar/server/tools/health.py` -- 2 health tool implementations
- `packages/core/mcp_hangar/server/tools/discovery.py` -- 5 discovery tool implementations
- `packages/core/mcp_hangar/server/tools/groups.py` -- 2 group tool implementations
- `packages/core/mcp_hangar/server/tools/batch/__init__.py` -- hangar_call implementation
- `packages/core/mcp_hangar/server/tools/batch/models.py` -- batch constants and models
- `packages/core/mcp_hangar/server/tools/continuation.py` -- 2 continuation tool implementations
- `packages/core/mcp_hangar/server/config.py` -- configuration loading with all keys/defaults
- `packages/core/mcp_hangar/facade.py` -- Hangar/SyncHangar/HangarConfig complete API
- `packages/core/mcp_hangar/domain/model/provider_group.py` -- ProviderGroup aggregate
- `packages/core/mcp_hangar/domain/model/load_balancer.py` -- 5 load balancing strategies
- `packages/core/mcp_hangar/domain/model/circuit_breaker.py` -- CircuitBreaker implementation
- `packages/core/mcp_hangar/domain/model/provider_config.py` -- ToolsConfig, ProviderConfig
- `packages/core/mcp_hangar/domain/value_objects/config.py` -- IdleTTL, HealthCheckInterval ranges
- `packages/core/mcp_hangar/domain/value_objects/health.py` -- MemberWeight, MemberPriority, CircuitBreakerConfig
- `packages/core/mcp_hangar/domain/value_objects/truncation.py` -- TruncationConfig
- `packages/core/mcp_hangar/bootstrap/runtime.py` -- MCP_ env vars
- `packages/core/mcp_hangar/server/cli/commands/serve.py` -- CLI env vars
- `packages/core/mcp_hangar/server/bootstrap/observability.py` -- observability env vars
- `config.yaml.example` -- annotated example config
- `config.max.yaml` -- maximum config with all features
- `mkdocs.yml` -- navigation structure and theme config

### Secondary (MEDIUM confidence)

- `docs/reference/cli.md`, `docs/reference/hot-reload.md`, `docs/guides/BATCH_INVOCATIONS.md` -- existing doc style patterns

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH -- no new libraries needed, MkDocs-material already configured
- Architecture: HIGH -- file locations and nav structure follow established patterns
- Content data: HIGH -- all content extracted directly from verified source code
- Pitfalls: HIGH -- identified from direct code inspection

**Research date:** 2026-02-28
**Valid until:** Indefinite (documenting existing codebase, not external libraries)

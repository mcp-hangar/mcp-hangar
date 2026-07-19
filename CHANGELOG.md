# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.5.1...v1.6.0) (2026-07-19)


### Added

* **core:** add L7 egress policy engine ([#526](https://github.com/mcp-hangar/mcp-hangar/issues/526)) ([575602d](https://github.com/mcp-hangar/mcp-hangar/commit/575602d1fc28b8f784169157470e2d6e3ddd2ec7))
* **core:** enforce L7 egress policy at the tool-invocation chokepoint ([#527](https://github.com/mcp-hangar/mcp-hangar/issues/527)) ([2d22ad9](https://github.com/mcp-hangar/mcp-hangar/commit/2d22ad99b1ad5458ae61d9e715941540916abb9d))
* **core:** receive compiled L7 egress policy over the REST API ([#528](https://github.com/mcp-hangar/mcp-hangar/issues/528)) ([0825a47](https://github.com/mcp-hangar/mcp-hangar/commit/0825a47bbf6888b4f88e126c942a824b060649a8))
* **observability:** add telemetry-health alerts (OTLP export + discovery validation) ([#541](https://github.com/mcp-hangar/mcp-hangar/issues/541)) ([393f492](https://github.com/mcp-hangar/mcp-hangar/commit/393f492229f0a02762153a0b6b3a1482f2bdc138))
* **observability:** trace the upstream call boundary (client spans, stdio propagation, sampler) ([#537](https://github.com/mcp-hangar/mcp-hangar/issues/537)) ([63bad07](https://github.com/mcp-hangar/mcp-hangar/commit/63bad0737305de542881974ca8cd4bd4682d177f))
* **observability:** wire transport message metrics; drop never-emitted pool/SSE gauges ([#540](https://github.com/mcp-hangar/mcp-hangar/issues/540)) ([9d3ed15](https://github.com/mcp-hangar/mcp-hangar/commit/9d3ed15764d0d013b850a59f8e055b426b0b4d0d))


### Fixed

* **core:** consolidate discovery metrics onto the scraped registry ([#534](https://github.com/mcp-hangar/mcp-hangar/issues/534)) ([d699ff0](https://github.com/mcp-hangar/mcp-hangar/commit/d699ff027d6259dd3751ecfb9f6434b1b3ffdb53))
* **core:** emit the cost-attribution metrics ([#535](https://github.com/mcp-hangar/mcp-hangar/issues/535)) ([275de80](https://github.com/mcp-hangar/mcp-hangar/commit/275de802cf6757b61f580d9252420ff479a6c30d))
* **core:** L7 argument scan fails closed on unserializable arguments ([#529](https://github.com/mcp-hangar/mcp-hangar/issues/529)) ([a14bb2e](https://github.com/mcp-hangar/mcp-hangar/commit/a14bb2ebc6580ab80098ef07fb1bd4242ac42b3a))
* **core:** redact secret values in logs and the log buffer ([#530](https://github.com/mcp-hangar/mcp-hangar/issues/530)) ([1374da8](https://github.com/mcp-hangar/mcp-hangar/commit/1374da8730e3c5af84bf32e0a9c128d863883170))
* **core:** scrub Langfuse tool inputs/outputs by default ([#531](https://github.com/mcp-hangar/mcp-hangar/issues/531)) ([98a2cb9](https://github.com/mcp-hangar/mcp-hangar/commit/98a2cb907692f77547c4a2ab639bc2e9dbf190c5))
* **core:** wire connections_active; delete the redundant connection metrics ([#536](https://github.com/mcp-hangar/mcp-hangar/issues/536)) ([81accc1](https://github.com/mcp-hangar/mcp-hangar/commit/81accc19a0680b2a1d5463b68420f77f5be490c5))
* **metrics:** stop poisoning the latency histogram; drop stream_id label ([#532](https://github.com/mcp-hangar/mcp-hangar/issues/532)) ([55abc52](https://github.com/mcp-hangar/mcp-hangar/commit/55abc5261627ad6101fddc229c271fea87fc1de0))
* **observability:** correlate logs with traces (trace_id/span_id) ([#533](https://github.com/mcp-hangar/mcp-hangar/issues/533)) ([29ea16b](https://github.com/mcp-hangar/mcp-hangar/commit/29ea16b09cf3fe751b0b296b1f0e608034e27c42))
* **observability:** mark a failed tool call's span as ERROR ([#544](https://github.com/mcp-hangar/mcp-hangar/issues/544)) ([43848e9](https://github.com/mcp-hangar/mcp-hangar/commit/43848e9349968219003a6975ea4056dd9098b5f7))
* **observability:** stop logging expected stdio shutdowns as errors ([#542](https://github.com/mcp-hangar/mcp-hangar/issues/542)) ([11ef2c7](https://github.com/mcp-hangar/mcp-hangar/commit/11ef2c7e5049c4951c68e16ca44db2419eb58a78))


### Changed

* **core:** collapse the vestigial enterprise plugin boundary ([#538](https://github.com/mcp-hangar/mcp-hangar/issues/538)) ([1813dcd](https://github.com/mcp-hangar/mcp-hangar/commit/1813dcdaecf29d4469ee0adb96d5555553a81ecc))
* **observability:** align tool-invocation spans to OTel GenAI/MCP semconv ([#539](https://github.com/mcp-hangar/mcp-hangar/issues/539)) ([d705c8f](https://github.com/mcp-hangar/mcp-hangar/commit/d705c8f20763137fda61bb7b537330c7d3357592))


### Security

* **core:** validate WebSocket handshake Origin/Host at the edge ([#524](https://github.com/mcp-hangar/mcp-hangar/issues/524)) ([403ec6c](https://github.com/mcp-hangar/mcp-hangar/commit/403ec6c700173faed3cf3da324993b0fc92d267c))

## [1.5.1](https://github.com/mcp-hangar/mcp-hangar/compare/v1.5.0...v1.5.1) (2026-07-16)


### Fixed

* **core:** resolve discovery/config review findings ([#481](https://github.com/mcp-hangar/mcp-hangar/issues/481), [#483](https://github.com/mcp-hangar/mcp-hangar/issues/483), [#484](https://github.com/mcp-hangar/mcp-hangar/issues/484)) ([#493](https://github.com/mcp-hangar/mcp-hangar/issues/493)) ([1600c54](https://github.com/mcp-hangar/mcp-hangar/commit/1600c543ecf6e3fa8d8af1b63f842c1339e46740))
* **repo:** add basic client scope to keycloak example realm so tokens carry sub ([#476](https://github.com/mcp-hangar/mcp-hangar/issues/476)) ([2c1e9f4](https://github.com/mcp-hangar/mcp-hangar/commit/2c1e9f4d3d673fb142cf5d8e217a8d8f89dc2da6))
* **security:** require mcp&gt;=1.28.1 (CVE-2026-59950) ([#497](https://github.com/mcp-hangar/mcp-hangar/issues/497)) ([5ba85d1](https://github.com/mcp-hangar/mcp-hangar/commit/5ba85d18c5c655d47092906e6577597528afa4dc))

## [Unreleased]

### Added

* **core:** add the L7 egress policy engine (`domain.policies.egress_l7`): deterministic tool-call matching (glob allow / deny / require-approval with a policy default action) and argument scanning (named secret-pattern groups reusing the output redactor's value-regexes, plus a payload-size limit). Pure and deterministic — no ML. This is the core-side half of `MCPEgressPolicy` ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53))
* **core:** receive the compiled L7 egress policy from the operator over the REST API — `POST/PUT /api/mcp_servers/{id}/l7_policy` (set/replace) and `DELETE` (clear), guarded by the `mcp_servers:write` permission. Adds `L7Policy.from_dict` (parses the operator's camelCase wire form) and a `SetL7PolicyCommand`/handler that calls `McpServer.set_l7_policy`, closing the operator→core transport so an `MCPEgressPolicy` drives L7 enforcement end to end ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53))
* **core:** enforce the L7 egress policy at the tool-invocation chokepoint. `McpServer` carries an optional L7 policy; `invoke_tool` evaluates every call against it before waking the server or touching the upstream — a denied call raises `EgressPolicyDeniedError`, an approval-gated one raises `EgressPolicyApprovalRequiredError`, and neither reaches the wire. No policy attached means no enforcement (unchanged behavior). Populating the policy from the operator's compiled `MCPEgressPolicy` is the remaining transport step ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53))
* **observability:** trace the upstream call boundary. Outgoing MCP RPCs (`tools/call`, `tools/list`, `initialize`) are now `SpanKind.CLIENT` spans named per OTel GenAI/MCP semconv (`execute_tool {tool}`, with `gen_ai.tool.name` / `gen_ai.operation.name` / `mcp.method.name`) so an upstream's server span parents correctly to the gateway. The stdio transport now propagates W3C trace context into the MCP `_meta` field, mirroring the HTTP header injection, so distributed tracing survives stdio upstreams too. `init_tracing` now honors `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` (`always_on`/`always_off`/`traceidratio`/`parentbased_*`), which the hand-built `TracerProvider` previously ignored despite the documented contract

* **observability:** add two telemetry-health Prometheus alerts (`monitoring/prometheus/alerts.yaml`): `MCPHangarTelemetryExportFailing` (fires on `rate(mcp_hangar_otlp_export_failures_total[5m]) > 0` — a silent OTLP export failure means traces are being dropped with no app-level error) and `MCPHangarDiscoveryValidationFailing` (discovered servers being rejected on validation). Closes a coverage gap for two emitted-but-unalerted signals; validated with `promtool check rules`

### Changed

* **observability:** align tool-invocation spans to OTel GenAI/MCP semantic conventions. The application-layer span is renamed `tool.invoke.{tool}` → `execute_tool {tool}` (matching the transport CLIENT span from the previous change), and the tool-name / token attributes move to semconv: `mcp.tool.name` → `gen_ai.tool.name`, `mcp.cost.input_tokens` / `mcp.cost.output_tokens` → `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`, with `gen_ai.operation.name` and `mcp.method.name` now also set. **Breaking for consumers that query the old span/OTLP-audit attribute names.** The Hangar-specific governance namespaces (`mcp.enforcement.*`, `mcp.risk.*`, `mcp.audit.*`, `mcp.cost.cents`/`model`/`currency`, `mcp.session.id`, …) are unchanged — they have no semconv equivalent. Also restores OTLP audit-log export, which a botched `Provider`→`McpServer` rename had silently disabled (the `LoggerMcpServer` import always failed, pinning `OTEL_LOGS_AVAILABLE` to false). Found continuing the observability audit

### Security

* **core:** Langfuse tracing now scrubs tool-call inputs and outputs by **default** (`scrub_inputs`/`scrub_outputs` default to true) — the exporter previously shipped full argument and result payloads to Langfuse unless explicitly disabled. Set them false to send full content for debugging. Found by the observability audit
* **core:** redact secret *values* (AWS/GitHub/Slack/Stripe keys, JWTs, bearer tokens, …) across the logging pipeline and the MCP-server log buffer. The value-level `OutputRedactor` is now a structlog processor (complementing the existing key-name redaction) and is applied to subprocess `stderr` at the source before it enters the buffer — so the `GET /mcp_servers/{id}/logs` API can no longer serve raw secrets that an MCP server printed to stderr. Long-string redaction stays off, so only recognizable token shapes are rewritten. Found by the observability audit
* **core:** L7 argument scanning now fails closed on un-serializable tool-call arguments (e.g. a circular reference) instead of raising — an unscannable payload is reported as a violation rather than crashing the evaluation — and skips serialization entirely when no argument rules are configured. Found by adversarial testing ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53))
* **security:** require `mcp>=1.28.1` to pull in the fix for CVE-2026-59950 (MCP Python SDK WebSocket server transport missing Host/Origin validation, HIGH). The published constraint was `mcp>=1.0.0`, so installs could still resolve a vulnerable SDK even though the dev lock had moved.
* **core:** validate WebSocket handshake `Origin`/`Host` at the Hangar ASGI edge before forwarding non-`/api/` connections to the SDK app (DNS-rebinding / cross-origin defense-in-depth, the CVE-2026-59950 class at our own trust boundary). Loopback is trusted; non-loopback is fail-closed — a present `Origin` must be allow-listed (`MCP_CORS_ORIGINS`), a missing one is allowed (non-browser client, auth still applies), and the `Host` must be in `MCP_TRUSTED_HOSTS` ([#498](https://github.com/mcp-hangar/mcp-hangar/issues/498))

### Fixed

* **metrics:** wire `mcp_hangar_connections_active` (set 1 when a server's client connects, 0 on close/shutdown) so the provider-details "Active Connections" panel has data, and **remove** the never-emitted `mcp_hangar_connections_total` / `mcp_hangar_connection_duration_seconds` — no dashboard or alert referenced them and they duplicated the server-lifecycle signals. Found by the observability audit
* **metrics:** wire the transport message metrics — `mcp_hangar_messages_sent` (by `method`), `mcp_hangar_messages_received` (by `type`: response/notification/error), and the `mcp_hangar_message_size_bytes` payload-size histogram (by `direction`) — at the stdio and HTTP transport boundaries, labeled per upstream server. These were defined but never emitted, so the protocol-level and payload-size panels stayed empty. **Removed** three never-emitted metrics that have nothing to populate them: `mcp_hangar_http_connection_pool_size` (httpx pool internals aren't exposed) and `mcp_hangar_http_sse_streams_active` / `mcp_hangar_http_sse_events` (the streaming-SSE reader path is unused — SSE responses are batch-parsed). Repurposed the dead "Active SSE Streams" dashboard panel to a messages-sent rate. Found by the observability audit
* **metrics:** emit the cost-attribution metrics (`mcp_hangar_cost_cents_total`, `mcp_hangar_cost_attributions_total`). The cost handler computed per-invocation cost via the `ICostAttributor` port and published a report event, but never fed the Prometheus metrics its docstring promised — so the governance dashboard's cost panels stayed empty even with a real attributor configured. Now wired (a no-op under the default `NullCostAttributor`). Found by the observability audit
* **metrics:** consolidate discovery metrics onto the single scraped registry. Discovery registrations, errors, validation failures, and validation durations were recorded only to a second `prometheus_client` registry that the `/metrics` endpoint never serialized — so they were silently dropped, and cycle/quarantine/deregistration were double-recorded. Removed the dead secondary system (`application/discovery/discovery_metrics.py`), added the two missing metrics (`mcp_hangar_discovery_validation_failures_total`, `mcp_hangar_discovery_validation_duration_seconds`) to the primary registry, and rewired the orchestrator through it. Found by the observability audit
* **observability:** stop logging expected stdio-server shutdowns as errors. When Hangar closes a subprocess server (idle-TTL expiry / explicit stop), `close()` sets the client closed before terminating, so the reader thread's `stdio_client_process_exited` (+ any drained stderr) was logged at ERROR on every graceful shutdown — inflating the error stream and any log-based alerting. These are now logged at `info` with `expected=true` when we initiated the exit; an unsolicited process death is still an ERROR. Found reviewing live logs in Loki
* **observability:** mark a failed tool call's span as ERROR. The batch executor handles failures as data (`CallResult.success=False`), so the `batch.call.{tool}` span never saw an exception and stayed UNSET — a failing tool call looked like a successful trace and couldn't be filtered as an error in Jaeger/Tempo. It now sets ERROR status (with the failure message) when the call fails. Added a NoOp-safe `mark_span_error` helper. Found reviewing the error path on the live stack
* **observability:** correlate logs with traces — every log record emitted inside an OpenTelemetry span now carries `trace_id`/`span_id`, so you can pivot from a log line to its trace. A no-op when tracing is off or there is no active span, and it never lets a tracing error break a log call. Found by the observability audit
* **metrics:** the tool-call latency histogram (`mcp_hangar_tool_call_duration_seconds`) no longer records a 0-second observation for every failed call — failures carried no real duration and poisoned the p50/p95/p99 percentiles. Duration is observed only for successful calls; failures are still counted via `mcp_hangar_tool_call_errors_total`. Found by the observability audit
* **metrics:** drop the unbounded `stream_id` label from `mcp_hangar_events_compacted_total` — stream IDs are per-stream identifiers and were a cardinality bomb. Compaction is now a fleet-wide counter. Found by the observability audit
* **core:** discovered `http`/`sse` containers now prefer the published host-port binding over the internal bridge-network IP, so they are reachable from the documented host-mode deployment ([#481](https://github.com/mcp-hangar/mcp-hangar/issues/481))
* **core:** allow a discovery-only `config.yaml` (`discovery.enabled: true`, no top-level `mcp_servers`) to load instead of raising ([#483](https://github.com/mcp-hangar/mcp-hangar/issues/483))
* **core:** log the transient "container has no IP" discovery skip at debug instead of warning ([#484](https://github.com/mcp-hangar/mcp-hangar/issues/484))

### Removed

* **core:** retire the Hangar Cloud connector (`src/mcp_hangar/cloud/`), the `POST /agent/policy` endpoint, the `--cloud-key`/`--cloud-url` CLI flags, and the `agent` RBAC role, as the hangar-agent / Hangar Cloud product tier is retired. `PolicyPushRejected` is intentionally kept (deprecated, producer-less) so already-persisted events still replay; `policy:write` remains and is granted via the `admin` role.

## [1.5.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.4.0...v1.5.0) (2026-07-15)


### Added

* **cli:** add `auth bootstrap-admin` command (durable initial admin) ([#463](https://github.com/mcp-hangar/mcp-hangar/issues/463)) ([57b21fc](https://github.com/mcp-hangar/mcp-hangar/commit/57b21fc5816b8daf980c7272f4bae0fc94b3e9be)), closes [#451](https://github.com/mcp-hangar/mcp-hangar/issues/451) [#452](https://github.com/mcp-hangar/mcp-hangar/issues/452)
* **core:** add interceptor/invoke + phase-aware hooks, pinned to MCP `modelcontextprotocol/modelcontextprotocol#2624` ([#400](https://github.com/mcp-hangar/mcp-hangar/issues/400)) ([3a0e2b5](https://github.com/mcp-hangar/mcp-hangar/commit/3a0e2b5d4df67821aa743fb69ff64ab037b5b28e))
* **core:** add server/discover entry point backed by the per-tenant projection ([#407](https://github.com/mcp-hangar/mcp-hangar/issues/407)) ([6713cbd](https://github.com/mcp-hangar/mcp-hangar/commit/6713cbdef243977d36e3bfc30f24f4c3dc0c758d))
* **core:** configurable command-bus rate limit via config.yaml ([#398](https://github.com/mcp-hangar/mcp-hangar/issues/398)) ([a891496](https://github.com/mcp-hangar/mcp-hangar/commit/a89149610ebbf2337bc97253483840875e3339f8))
* **core:** emit task-lifecycle audit events (created/input_required/completed/failed/cancelled) ([#399](https://github.com/mcp-hangar/mcp-hangar/issues/399)) ([eb399bc](https://github.com/mcp-hangar/mcp-hangar/commit/eb399bcf8d0075721f95ba9a9abb9f3738d914f5))
* **observability:** meter OTLP export failures and document the tracing off-switch ([#419](https://github.com/mcp-hangar/mcp-hangar/issues/419)) ([515c57c](https://github.com/mcp-hangar/mcp-hangar/commit/515c57c7538e0c5959fd1f8fe566572592448637))
* **security:** atomically bootstrap the first API-key admin ([#456](https://github.com/mcp-hangar/mcp-hangar/issues/456)) ([9239705](https://github.com/mcp-hangar/mcp-hangar/commit/92397054a3d181c3ffe713a6c4022de6fad32250))


### Fixed

* **ci:** repair actionlint gate (broken action ref) and the YAML it flags ([#287](https://github.com/mcp-hangar/mcp-hangar/issues/287)) ([ee5de14](https://github.com/mcp-hangar/mcp-hangar/commit/ee5de144eea5c0fc3d8cb3dbefcbb7238c67b152))
* **cli:** accept --config after serve and fix generated Claude Desktop config ([#420](https://github.com/mcp-hangar/mcp-hangar/issues/420)) ([9068161](https://github.com/mcp-hangar/mcp-hangar/commit/9068161b4a6e0c2a72579841550ba081d3f440b5)), closes [#417](https://github.com/mcp-hangar/mcp-hangar/issues/417)
* **core:** clarify that mode:docker requires a host container CLI ([#430](https://github.com/mcp-hangar/mcp-hangar/issues/430)) ([732de25](https://github.com/mcp-hangar/mcp-hangar/commit/732de255652b8a579cac97392230457cf3acb25b)), closes [#429](https://github.com/mcp-hangar/mcp-hangar/issues/429)
* **core:** config.yaml.example uses mcp_servers: (loader requires it, not providers:) ([#458](https://github.com/mcp-hangar/mcp-hangar/issues/458)) ([498b312](https://github.com/mcp-hangar/mcp-hangar/commit/498b312fcf993041abfebd462688cf939faa4a0d)), closes [#457](https://github.com/mcp-hangar/mcp-hangar/issues/457)
* **core:** expose bootstrapped discovery through REST ([#442](https://github.com/mcp-hangar/mcp-hangar/issues/442)) ([1c2280c](https://github.com/mcp-hangar/mcp-hangar/commit/1c2280c2870a1718743b5f80af2090e2468093a4))
* **core:** fail fast when SQLite event store is unavailable ([#438](https://github.com/mcp-hangar/mcp-hangar/issues/438)) ([a1be5db](https://github.com/mcp-hangar/mcp-hangar/commit/a1be5db4a5f965d06adabf97dde0420c2ad2c59b))
* **core:** fail-fast on unwritable SQLite event store and add a durability readiness check ([#448](https://github.com/mcp-hangar/mcp-hangar/issues/448)) ([77f84cc](https://github.com/mcp-hangar/mcp-hangar/commit/77f84ccff9560a7d0eaf93a70f0fda9ce49a8d6a))
* **core:** group circuit breaker no longer blocks a healthy remaining member ([#426](https://github.com/mcp-hangar/mcp-hangar/issues/426)) ([0b9cdc8](https://github.com/mcp-hangar/mcp-hangar/commit/0b9cdc89b9e8b0de8aa1349aecc39ba4e10fa1eb)), closes [#425](https://github.com/mcp-hangar/mcp-hangar/issues/425)
* **core:** make EventStoreConfigurationError a ConfigurationError subclass ([#459](https://github.com/mcp-hangar/mcp-hangar/issues/459)) ([42cce1a](https://github.com/mcp-hangar/mcp-hangar/commit/42cce1ada6a2a70375a7e338405e7de2508defbb))
* **core:** re-pin interceptor schema to 99bc7c9 and reconcile SEP-2133 capability key ([#405](https://github.com/mcp-hangar/mcp-hangar/issues/405)) ([c972adf](https://github.com/mcp-hangar/mcp-hangar/commit/c972adf04aea89afe1fba49665e26f69ea5180b6))
* **core:** run discovery on a dedicated lifecycle loop ([#446](https://github.com/mcp-hangar/mcp-hangar/issues/446)) ([4eee12c](https://github.com/mcp-hangar/mcp-hangar/commit/4eee12c2efe9490e5b41602f07da6301c3df3b95))
* **core:** treat MCP tool result isError as a failure ([#427](https://github.com/mcp-hangar/mcp-hangar/issues/427)) ([8ed7405](https://github.com/mcp-hangar/mcp-hangar/commit/8ed7405abb7b56e4e5744e2d71b199178f73d60f)), closes [#423](https://github.com/mcp-hangar/mcp-hangar/issues/423)
* **core:** unblock concurrent cold-start waiters ([#440](https://github.com/mcp-hangar/mcp-hangar/issues/440)) ([9721349](https://github.com/mcp-hangar/mcp-hangar/commit/972134906eca086bea028c0ff5f77e6d631c7958))
* **core:** use supported lifecycle API during reload ([#441](https://github.com/mcp-hangar/mcp-hangar/issues/441)) ([98f09f1](https://github.com/mcp-hangar/mcp-hangar/commit/98f09f1cc5ec8949ce01cbf8660d809c406e76e1))
* **security:** read the Authorization header case-insensitively in JWTAuthenticator ([#472](https://github.com/mcp-hangar/mcp-hangar/issues/472)) ([7863848](https://github.com/mcp-hangar/mcp-hangar/commit/78638482741b7ca6e5b341a678453d6820ab3519))

## [Unreleased]

### Added
- **cli:** `mcp-hangar auth bootstrap-admin --config PATH --principal PRINCIPAL` grants the one-time initial global admin using the server's own durable auth backend (reuses `bootstrap_auth()`, never an in-memory store). Fails closed when auth is disabled, anonymous access is allowed, or the storage driver is non-durable (`memory`/`event_sourcing`); a second run is refused without mutating storage. No credential is printed -- the grant is a global admin role for an existing external principal ([#451](https://github.com/mcp-hangar/mcp-hangar/issues/451))

### Fixed
- **security:** `JWTAuthenticator` read the `Authorization` header case-sensitively (`get("Authorization")`), but the HTTP auth middleware lowercases header names (ASGI headers already are), so `supports()` never matched a bearer request -- every valid OIDC/JWT token over `serve --http` was rejected as `auth_method: none` and OIDC bearer auth was non-functional on the HTTP surface. Now reads the header case-insensitively, matching `ApiKeyAuthenticator` ([#471](https://github.com/mcp-hangar/mcp-hangar/issues/471))
- **security:** the SQLite role store seeded built-in roles with `INSERT OR REPLACE`, which deletes the conflicting row; because `role_assignments.role_name` has `ON DELETE CASCADE`, re-initializing the store (every process start / `bootstrap_auth`) silently cascade-wiped every assignment to a built-in role -- dropping the bootstrapped admin on the next restart. The seed (and `add_role`) now upsert in place via `ON CONFLICT(name) DO UPDATE`, matching the PostgreSQL store, so assignments survive ([#451](https://github.com/mcp-hangar/mcp-hangar/issues/451))
- **core:** `EventStoreConfigurationError` now subclasses the domain `ConfigurationError` (was `RuntimeError`), so the event-store fail-fast surfaces as a configuration error at the config boundary; realigned the enterprise-boundary tests that asserted the pre-`#428` exception type/message, unbreaking `CI - Core` on `main`
- **core:** `config.yaml.example` used a `providers:` server section, but the loader requires `mcp_servers:` and raises `Invalid configuration: missing 'mcp_servers' section` -- copying the example verbatim failed to start. Renamed to `mcp_servers:` (and the `mcp_servers.*.max_concurrency` doc path)

- **core:** fail fast when the SQLite event store cannot be initialized (path not writable / backend unavailable) instead of silently degrading to a non-durable in-memory store and losing the audit/event-sourcing trail; opt into the non-durable fallback with `event_store.driver: memory` or `event_store.allow_memory_fallback: true`. Also adds an `event_store_durability` readiness check so `/health/ready` returns 503 when the store degraded to in-memory while a durable driver was configured ([#428](https://github.com/mcp-hangar/mcp-hangar/issues/428))
### Changed

- **core:** clarify that `mode: docker`/`container` requires a podman or docker CLI on the host; the no-runtime start error and `config.yaml.example` now state that container mode is unsupported inside the stock Hangar container image and advise running in host mode or using a subprocess provider ([#429](https://github.com/mcp-hangar/mcp-hangar/issues/429))
### Fixed

- **core:** treat a backend MCP tool result with `isError: true` as a tool failure instead of a success, so per-call results, batch `succeeded`/`failed` counts, health, and `ToolInvocationFailed` events reflect reality ([#423](https://github.com/mcp-hangar/mcp-hangar/issues/423))

## [1.4.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.3.0...v1.4.0) (2026-06-29)


### Added

* **core:** per-tenant canary and version routing for groups ([#283](https://github.com/mcp-hangar/mcp-hangar/issues/283)) ([3410801](https://github.com/mcp-hangar/mcp-hangar/commit/341080111b2368d95a1c61f097fb3c94159c6c68)), closes [#275](https://github.com/mcp-hangar/mcp-hangar/issues/275) [#226](https://github.com/mcp-hangar/mcp-hangar/issues/226)
* **core:** per-tenant tool digest pinning on the call path ([#276](https://github.com/mcp-hangar/mcp-hangar/issues/276)) ([0d2b2f2](https://github.com/mcp-hangar/mcp-hangar/commit/0d2b2f26161314bbe40e17d1669010f573e9bff2)), closes [#233](https://github.com/mcp-hangar/mcp-hangar/issues/233) [#226](https://github.com/mcp-hangar/mcp-hangar/issues/226)
* **observability:** activate availability and transport alerts ([#269](https://github.com/mcp-hangar/mcp-hangar/issues/269)) ([774cb8f](https://github.com/mcp-hangar/mcp-hangar/commit/774cb8f27b4ebce379ccee69dd462f97c3053770)), closes [#268](https://github.com/mcp-hangar/mcp-hangar/issues/268)
* **observability:** add governance dashboard and alerts for cost, security, and concurrency metrics ([#267](https://github.com/mcp-hangar/mcp-hangar/issues/267)) ([ced19dc](https://github.com/mcp-hangar/mcp-hangar/commit/ced19dc1d0dbe9cdb10636a0417689ee258a83d8)), closes [#261](https://github.com/mcp-hangar/mcp-hangar/issues/261) [#266](https://github.com/mcp-hangar/mcp-hangar/issues/266)
* **security:** bind token audience to resource URI (RFC 8707) ([#274](https://github.com/mcp-hangar/mcp-hangar/issues/274)) ([783b34b](https://github.com/mcp-hangar/mcp-hangar/commit/783b34b2837c379a66e6ae457e75185615ade1f8)), closes [#255](https://github.com/mcp-hangar/mcp-hangar/issues/255) [#253](https://github.com/mcp-hangar/mcp-hangar/issues/253)
* **security:** multi-issuer trust registry for OAuth Resource Server ([#273](https://github.com/mcp-hangar/mcp-hangar/issues/273)) ([2a7bd3e](https://github.com/mcp-hangar/mcp-hangar/commit/2a7bd3e6b02faa92fd7441fabe2a35d54e6c27b3)), closes [#254](https://github.com/mcp-hangar/mcp-hangar/issues/254) [#253](https://github.com/mcp-hangar/mcp-hangar/issues/253)


### Fixed

* **core:** cost counters emit a doubled _total suffix ([#266](https://github.com/mcp-hangar/mcp-hangar/issues/266)) ([b05cd5c](https://github.com/mcp-hangar/mcp-hangar/commit/b05cd5c7800d5bd3f9dbbb297d6ec5104fd962d9)), closes [#265](https://github.com/mcp-hangar/mcp-hangar/issues/265)
* **core:** harden per-tenant digest pinning (per-server enforcement, tenant_id, tests) ([#280](https://github.com/mcp-hangar/mcp-hangar/issues/280)) ([066bf97](https://github.com/mcp-hangar/mcp-hangar/commit/066bf97dabdf3fb967d38fe9f8370b485c56e208)), closes [#278](https://github.com/mcp-hangar/mcp-hangar/issues/278) [#226](https://github.com/mcp-hangar/mcp-hangar/issues/226)
* **core:** select a group member on the invoke path ([#282](https://github.com/mcp-hangar/mcp-hangar/issues/282)) ([532afd8](https://github.com/mcp-hangar/mcp-hangar/commit/532afd86d43d771ef33c671cc28c2725bbb711df)), closes [#281](https://github.com/mcp-hangar/mcp-hangar/issues/281) [#275](https://github.com/mcp-hangar/mcp-hangar/issues/275)
* **observability:** align monitoring dashboards and alerts with mcp_server rename ([#263](https://github.com/mcp-hangar/mcp-hangar/issues/263)) ([db3f7a6](https://github.com/mcp-hangar/mcp-hangar/commit/db3f7a6e348b595516b57f94d70a1e557e47eb5e)), closes [#260](https://github.com/mcp-hangar/mcp-hangar/issues/260)
* **security:** reject non-string iss claim instead of raising 500 ([#279](https://github.com/mcp-hangar/mcp-hangar/issues/279)) ([ea1035f](https://github.com/mcp-hangar/mcp-hangar/commit/ea1035f6212e3d35a4f391c962048c7cba8e3bf4)), closes [#277](https://github.com/mcp-hangar/mcp-hangar/issues/277)


### Changed

* **observability:** remove dead ObservabilityMetrics registry ([#272](https://github.com/mcp-hangar/mcp-hangar/issues/272)) ([b93382a](https://github.com/mcp-hangar/mcp-hangar/commit/b93382a0ac3835cf102d3ee4595bd0fc974a7372)), closes [#271](https://github.com/mcp-hangar/mcp-hangar/issues/271)

## [Unreleased]

### Changed

- **core:** document the static `tools:` list as a pre-start visibility projection (the provider's dynamic `tools/list` is authoritative and replaces it at start) and log a warning naming any statically pre-configured tool the provider does not return (#415)
- **core:** **BREAKING** relicense from BSL 1.1 dual-license to MIT; all enterprise features are now freely available (#198)
- **core:** remove `LicenseTier` enum, `LicenseValidation`, and license-key gating from bootstrap; `load_enterprise_modules` loads unconditionally (#196)
- **core:** `HANGAR_LICENSE_KEY` env var is deprecated and emits `DeprecationWarning` when set (#196)
- **core:** `EnterpriseComponents` no longer carries a `license_tier` field; `ApplicationContext.license_tier` removed (#196)
- **core:** reject tool entries with missing, empty, or non-string `name` field in `compute_tool_digest` (#172)
- Public documentation migrated to dedicated [docs repository](https://github.com/mcp-hangar/docs). Internal docs remain in `docs/internal/`.

### Fixed

- **core:** run discovery on a dedicated lifecycle event loop so blocking discovery sources cannot block HTTP serving and shutdown awaits cleanup on the same loop (#436)
- **core:** expose bootstrapped discovery sources and pending providers through the canonical `/api/discovery` REST endpoint prefix (#434)
- **core:** reload configured mcp_servers through their supported shutdown lifecycle API and fail the reload when the old runtime cannot be stopped (#433)
- **core:** allow every concurrent cold-start waiter to invoke after the shared startup succeeds instead of timing out while the provider reaches READY (#435)
- **core:** fail startup when a configured SQLite event store is unavailable instead of silently falling back to volatile memory storage (#428)

### Removed

- **core:** delete `enterprise/auth/license.py` (HMAC license-key validator) (#196)
- **core:** delete `src/mcp_hangar/domain/value_objects/license.py` (`LicenseTier` enum) (#196)
- **core:** delete `enterprise/LICENSE.BSL` and `CLA.md` (#194, #197)
- **core:** remove CLA references from contributing guides (#197)
- **core:** strip BSL prose from `CONTRIBUTING.md`, `ROADMAP.md`, enterprise docstrings, and `PRODUCT_ARCHITECTURE.md` decision log (#195)
- **observability:** remove unused `Metrics.COLD_STARTS_TOTAL`, `Metrics.EGRESS_BLOCKED_TOTAL`, and `Metrics.PROVIDERS_QUARANTINED` constants — they had no backing metric in `metrics.py`

### Added

- **security:** atomically bootstrap the first API-key administrator in durable SQLite and PostgreSQL auth stores (#450)
- **core:** reconcile the interceptor surface with MCP PR #2624 — add `interceptor/invoke`, hook objects carrying `events` + `phase` (`request`/`response`), and phase-aware hook delivery on the request/response path. Opt-in and behind capability negotiation (header `MCP-Interceptor-Ext: io.modelcontextprotocol/interceptors` or `?ext=io.modelcontextprotocol/interceptors`); the default `interceptors/list` shape is unchanged. Pinned to PR #2624 head `8029c78` (OPEN — wire format may still move) (#317, #401)
- **core:** emit task-lifecycle audit events (`TaskCreated`, `TaskInputRequired`, `TaskCompleted`, `TaskFailed`, `TaskCancelled`) carrying `tenant_id` + `task_id` + `correlation_id`; the audit trail records all five and is reconstructable per `task_id` (#321)
- **core:** configurable command-bus rate limit via `config.yaml` `rate_limit.rps` / `rate_limit.burst`; config values take precedence over the `MCP_RATE_LIMIT_RPS` / `MCP_RATE_LIMIT_BURST` env vars, which remain as a fallback (#395)
- **tests:** schema validation for `interceptors/list` response against local JSON Schema derived from SEP-1763 (pinned @ `99bc7c9`) (#185, #401)
- **core:** add a SEP-2575 (Stateless MCP) `server/discover` entry point backed by the existing per-tenant projection read-model (#237). It returns the tenant-scoped tool surface — identical to the tenant's `tools/list` projection — alongside `supportedVersions`, `capabilities`, and `serverInfo`, so a stateless client can discover exactly the tools its tenant may call in one call. Tenant scoping and isolation are inherited from the projection (tenant A never sees tenant B's tools) (#290)
- **observability:** add `mcp_hangar_otlp_export_failures_total` counter, incremented via a `SpanExporter` decorator when an OTLP span-export batch fails (collector unreachable/export error), so otherwise-silent background export failures and dropped spans are observable on `/metrics`; document the `MCP_TRACING_ENABLED=false` off-switch for running locally without a collector (#402)
- **observability:** add `mcp_hangar_otlp_export_failures_total` counter, incremented via a `SpanExporter` decorator when an OTLP span-export batch fails (collector unreachable/export error), so otherwise-silent background export failures and dropped spans are observable on `/metrics`; document the `MCP_TRACING_ENABLED=false` off-switch for running locally without a collector (#418)

### Fixed

- **core:** re-pin the interceptor JSON schema (`5bd7ab4` → `99bc7c9`) and reconcile the capability-negotiation key with the SEP-2133 extensions format adopted upstream in experimental-ext-interceptors #25; the `interceptor/invoke` + negotiated `interceptors/list` gate now keys on `io.modelcontextprotocol/interceptors` (was `sep-2624`), so clients negotiating per current upstream reach the gate. Off-by-default posture preserved (#401)
- **core:** group circuit breaker no longer blocks member selection while a healthy member remains in rotation; the group CB now only vetoes selection when no member is in rotation (the group genuinely down), so an evicted primary failing over to a healthy backup is served instead of returning "No available member" (#425)
- **cli:** accept `--config`/`-c` on the `serve` subcommand so `mcp-hangar serve --config X` no longer fails with "No such option"; emit the unambiguous global-first arg order (`["--config", path, "serve"]`) in the generated Claude Desktop config so `mcp-hangar init` produces an entry that actually starts (#417)

## [1.3.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.3...v1.3.0) (2026-06-23)


### Highlights

**Per-tenant tool governance for external agents.** Hangar can now act as a front
door: external agents authenticate over OAuth (discoverable per RFC 9728), are
identified per tenant, and see and invoke only the tools their tenant is allowed —
enforced on every call, independent of the client's cached tool list.

- **Front-door mode** (`tool_access.mode: front_door`) — opt-in. Unauthenticated
  callers are denied; the default `egress` behavior is unchanged.
- **Per-tenant tool access** — member-scope allow/deny policy resolved on the live
  call path.
- **Tool withdrawal** — withdraw a tool for a tenant via config (reload) or the
  runtime admin API; rejected at call time. The guarantee is per-process-after-reload
  (fleet-wide synchronous withdrawal is future work).
- **Flat tool re-export** — in front-door mode, external agents see clean backend
  tool names instead of the `hangar_*` meta-API.
- **OAuth Resource Server discovery** (RFC 9728) — Protected Resource Metadata and a
  `WWW-Authenticate` challenge advertise the authorization server. Hangar validates
  tokens; it does not issue them. Multi-issuer trust and audience binding are
  tracked as follow-ups.


### Added

* **core:** add front_door fail-closed default for unauthenticated calls ([#242](https://github.com/mcp-hangar/mcp-hangar/issues/242)) ([b4d3200](https://github.com/mcp-hangar/mcp-hangar/commit/b4d32002a12e8fdb82b212dfdb13c5a83910a5bb)), closes [#236](https://github.com/mcp-hangar/mcp-hangar/issues/236)
* **core:** add runtime tool withdraw/restore mutation API ([#246](https://github.com/mcp-hangar/mcp-hangar/issues/246)) ([b72b43e](https://github.com/mcp-hangar/mcp-hangar/commit/b72b43e8f9ab4554245ad9f501c915c0c1243ac6)), closes [#235](https://github.com/mcp-hangar/mcp-hangar/issues/235)
* **core:** add tenant_id to CallerIdentity from JWT claim ([#238](https://github.com/mcp-hangar/mcp-hangar/issues/238)) ([0d85e36](https://github.com/mcp-hangar/mcp-hangar/commit/0d85e3669c50fa20e8e16a56c7bc123d9ec6cd4c)), closes [#228](https://github.com/mcp-hangar/mcp-hangar/issues/228)
* **core:** add ToolProjectionRegistry read-model ([#237](https://github.com/mcp-hangar/mcp-hangar/issues/237)) ([93b00c4](https://github.com/mcp-hangar/mcp-hangar/commit/93b00c4e8a4a45172356dfb7879ceea91cd31930)), closes [#230](https://github.com/mcp-hangar/mcp-hangar/issues/230)
* **core:** enforce tool withdrawal on the call path ([#243](https://github.com/mcp-hangar/mcp-hangar/issues/243)) ([40dcb77](https://github.com/mcp-hangar/mcp-hangar/commit/40dcb77ee37cb0e8bdb870ce8d9a3840c1618da5)), closes [#231](https://github.com/mcp-hangar/mcp-hangar/issues/231)
* **core:** flat per-tenant tool re-export in front_door mode ([#252](https://github.com/mcp-hangar/mcp-hangar/issues/252)) ([a8ecd17](https://github.com/mcp-hangar/mcp-hangar/commit/a8ecd178a0f9cc3c4b62fe6bb0b4fcc8c8093d93)), closes [#232](https://github.com/mcp-hangar/mcp-hangar/issues/232)
* **core:** populate tool withdrawal from config (reload-driven overlay) ([#245](https://github.com/mcp-hangar/mcp-hangar/issues/245)) ([ba1b6af](https://github.com/mcp-hangar/mcp-hangar/commit/ba1b6af4975a017a72c37f23a6bf3891d28599c4)), closes [#244](https://github.com/mcp-hangar/mcp-hangar/issues/244)
* **core:** populate ToolProjectionRegistry from tool discovery ([#250](https://github.com/mcp-hangar/mcp-hangar/issues/250)) ([02adbc5](https://github.com/mcp-hangar/mcp-hangar/commit/02adbc5c9f97beff8308d07bed72562232ce0e13)), closes [#248](https://github.com/mcp-hangar/mcp-hangar/issues/248)
* **core:** resolve member-scope tool policy on the live call path ([#241](https://github.com/mcp-hangar/mcp-hangar/issues/241)) ([152ca0e](https://github.com/mcp-hangar/mcp-hangar/commit/152ca0e123eee67493a5a15d41bb1631af27504e)), closes [#229](https://github.com/mcp-hangar/mcp-hangar/issues/229)
* **security:** advertise OAuth Protected Resource Metadata (RFC 9728) ([#257](https://github.com/mcp-hangar/mcp-hangar/issues/257)) ([d5a6089](https://github.com/mcp-hangar/mcp-hangar/commit/d5a6089f7f9fae1174f772d95f78bbb1e19867a7)), closes [#256](https://github.com/mcp-hangar/mcp-hangar/issues/256)


### Fixed

* **core:** bind caller identity on the MCP request path ([#249](https://github.com/mcp-hangar/mcp-hangar/issues/249)) ([af636cd](https://github.com/mcp-hangar/mcp-hangar/commit/af636cda084eacbcd22666c5f17ffeb3c79be156)), closes [#247](https://github.com/mcp-hangar/mcp-hangar/issues/247)
* **core:** propagate request context into batch worker threads ([#239](https://github.com/mcp-hangar/mcp-hangar/issues/239)) ([bad09d7](https://github.com/mcp-hangar/mcp-hangar/commit/bad09d78a354750be59c19c2324a4eaebe97c343)), closes [#227](https://github.com/mcp-hangar/mcp-hangar/issues/227)
* **core:** satisfy mypy and ruff format CI gates ([#258](https://github.com/mcp-hangar/mcp-hangar/issues/258)) ([d7a2a53](https://github.com/mcp-hangar/mcp-hangar/commit/d7a2a53825df6f86803a2402bf70eaba01ab1eda))

## [1.2.3](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.2...v1.2.3) (2026-06-23)


### Fixed

* **core:** add auth/tls/http config serialization to to_config_dict() ([#209](https://github.com/mcp-hangar/mcp-hangar/issues/209)) ([0df37d6](https://github.com/mcp-hangar/mcp-hangar/commit/0df37d6a8f6ad3b0287a6cd07c1e2e8895d1e6f2))
* **security:** make _sanitize() recursive to strip nested secrets ([#210](https://github.com/mcp-hangar/mcp-hangar/issues/210)) ([cfd2a0f](https://github.com/mcp-hangar/mcp-hangar/commit/cfd2a0f863e5d3c812ea6a4d7e79657e287c91b6)), closes [#206](https://github.com/mcp-hangar/mcp-hangar/issues/206)

## [1.2.2](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.1...v1.2.2) (2026-05-17)


### Fixed

* **core:** remove CLA nav entry and fix ruff formatting in cef_formatter ([753d485](https://github.com/mcp-hangar/mcp-hangar/commit/753d485617781b3bfcf7c06c3540d3e0dfd071e9))


### Changed

* **core:** absorb enterprise/ into src/mcp_hangar/ ([#201](https://github.com/mcp-hangar/mcp-hangar/issues/201)) ([010f2a0](https://github.com/mcp-hangar/mcp-hangar/commit/010f2a01f55130596a8934f56f5fcf65bff05229))
* **docs:** move adr/AGENTS.md to docs/internal/ADR_AGENTS.md ([4be7c4f](https://github.com/mcp-hangar/mcp-hangar/commit/4be7c4f2172295e5dff87bd47d3c6ee3d9f42c2e))

## [1.2.1](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.0...v1.2.1) (2026-05-11)


### Fixed

* **core:** disambiguate interceptors/list instance names ([#190](https://github.com/mcp-hangar/mcp-hangar/issues/190)) ([4e4a86c](https://github.com/mcp-hangar/mcp-hangar/commit/4e4a86cc6a89ce3115b91709f6af534f6ccb2403)), closes [#176](https://github.com/mcp-hangar/mcp-hangar/issues/176)
* **core:** replace json.dumps with RFC 8785 JCS canonicalization in compute_tool_digest ([#186](https://github.com/mcp-hangar/mcp-hangar/issues/186)) ([5626ef8](https://github.com/mcp-hangar/mcp-hangar/commit/5626ef87643714b2fa80af56ebb7f0b2c0270a76))
* **core:** treat empty values as absent in tool digest computation ([#188](https://github.com/mcp-hangar/mcp-hangar/issues/188)) ([fc93d13](https://github.com/mcp-hangar/mcp-hangar/commit/fc93d136887ba3cc86b4a50f47e843384e57fd9b)), closes [#173](https://github.com/mcp-hangar/mcp-hangar/issues/173)


### Changed

* **core:** rename ALLOW_DEGRADED to ALLOW_UNVERIFIED in DigestUnknownPolicy ([#189](https://github.com/mcp-hangar/mcp-hangar/issues/189)) ([00a44b4](https://github.com/mcp-hangar/mcp-hangar/commit/00a44b4fddae32fec1538c6b0517eed6c1311756)), closes [#175](https://github.com/mcp-hangar/mcp-hangar/issues/175)

## [1.2.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.1.0...v1.2.0) (2026-05-11)


### Added

* **ci:** add actionlint workflow to validate workflow YAML ([#115](https://github.com/mcp-hangar/mcp-hangar/issues/115)) ([1c887d1](https://github.com/mcp-hangar/mcp-hangar/commit/1c887d1a2748b1e22eb56765603e4b0ee5d64454)), closes [#111](https://github.com/mcp-hangar/mcp-hangar/issues/111)
* **core:** add ADR-004 digest pinning domain types and standalone validator ([#123](https://github.com/mcp-hangar/mcp-hangar/issues/123)) ([ad1b179](https://github.com/mcp-hangar/mcp-hangar/commit/ad1b1799d47d09d2ba7f8498c198a0415d78ee2f))
* **core:** add hook-based event model and interceptors/list ([#136](https://github.com/mcp-hangar/mcp-hangar/issues/136)) ([aa4f05c](https://github.com/mcp-hangar/mcp-hangar/commit/aa4f05c61cebbf382da79d5779e290837f041964)), closes [#120](https://github.com/mcp-hangar/mcp-hangar/issues/120) [#118](https://github.com/mcp-hangar/mcp-hangar/issues/118)
* **core:** add IMutator, MutatorPipeline, and ResponseTruncator ([#137](https://github.com/mcp-hangar/mcp-hangar/issues/137)) ([750541b](https://github.com/mcp-hangar/mcp-hangar/commit/750541bba2cc7d1b0b5844dd05317748dadb1d88)), closes [#121](https://github.com/mcp-hangar/mcp-hangar/issues/121) [#118](https://github.com/mcp-hangar/mcp-hangar/issues/118)
* **core:** add wildcard event subscription patterns (ADR-005 P1) ([#138](https://github.com/mcp-hangar/mcp-hangar/issues/138)) ([a7ae07d](https://github.com/mcp-hangar/mcp-hangar/commit/a7ae07d49dd22c4df4573331f613044a82ce550d)), closes [#122](https://github.com/mcp-hangar/mcp-hangar/issues/122)


### Fixed

* **ci:** remove PR-only checks from required status checks on main ([#114](https://github.com/mcp-hangar/mcp-hangar/issues/114)) ([3e6ddd4](https://github.com/mcp-hangar/mcp-hangar/commit/3e6ddd4a520b724c2c9ce42b0c94c67ede497aea)), closes [#110](https://github.com/mcp-hangar/mcp-hangar/issues/110)
* **core:** register interceptors/list route on bootstrap FastMCP instance ([#154](https://github.com/mcp-hangar/mcp-hangar/issues/154)) ([069329c](https://github.com/mcp-hangar/mcp-hangar/commit/069329cc9325b4edc1cc7562567c431416622a63)), closes [#151](https://github.com/mcp-hangar/mcp-hangar/issues/151)
* **docs:** add cross-repo operator prerequisites for Kubernetes recipes ([#146](https://github.com/mcp-hangar/mcp-hangar/issues/146)) ([e9b5e69](https://github.com/mcp-hangar/mcp-hangar/commit/e9b5e6926f288b032485524c4196058fae443f18)), closes [#127](https://github.com/mcp-hangar/mcp-hangar/issues/127)
* **docs:** clean up Provider to McpServer artifacts in guides ([#148](https://github.com/mcp-hangar/mcp-hangar/issues/148)) ([f5d12d2](https://github.com/mcp-hangar/mcp-hangar/commit/f5d12d225f8ef5b6e4005bb2788ea4da4253bdbe)), closes [#133](https://github.com/mcp-hangar/mcp-hangar/issues/133)
* **docs:** correct hangar_call format, CLI usage, and endpoints in cookbook ([#143](https://github.com/mcp-hangar/mcp-hangar/issues/143)) ([b24f951](https://github.com/mcp-hangar/mcp-hangar/commit/b24f951b944317c79d57bdd3c7a62add64d125b0)), closes [#125](https://github.com/mcp-hangar/mcp-hangar/issues/125)
* **docs:** correct stale metrics section in OBSERVABILITY.md ([#141](https://github.com/mcp-hangar/mcp-hangar/issues/141)) ([936be63](https://github.com/mcp-hangar/mcp-hangar/commit/936be637ae05c6863ff9b52da63e3ffbc412aba0)), closes [#135](https://github.com/mcp-hangar/mcp-hangar/issues/135)
* **docs:** drop phantom CLI subcommands and fix stale metric names ([#149](https://github.com/mcp-hangar/mcp-hangar/issues/149)) ([edf28f2](https://github.com/mcp-hangar/mcp-hangar/commit/edf28f260a09fd7eb6c3fd13c1372fdafd6a1ace)), closes [#134](https://github.com/mcp-hangar/mcp-hangar/issues/134)
* **docs:** drop phantom config blocks from cookbook recipes ([#144](https://github.com/mcp-hangar/mcp-hangar/issues/144)) ([8df8955](https://github.com/mcp-hangar/mcp-hangar/commit/8df89554acf33d763bcfb060daa3b20acd679502)), closes [#126](https://github.com/mcp-hangar/mcp-hangar/issues/126)
* **docs:** drop phantom endpoints from REST_API, WEBSOCKETS, LOG_STREAMING ([#145](https://github.com/mcp-hangar/mcp-hangar/issues/145)) ([b178ee8](https://github.com/mcp-hangar/mcp-hangar/commit/b178ee87e74284951883d473e7c0408f58e58c12)), closes [#132](https://github.com/mcp-hangar/mcp-hangar/issues/132)
* **docs:** fix leftover drift in cookbook recipes 02/03/04 ([#155](https://github.com/mcp-hangar/mcp-hangar/issues/155)) ([f1826f3](https://github.com/mcp-hangar/mcp-hangar/commit/f1826f35d9d47336f48b04bf99f856c1e0e5ada6)), closes [#152](https://github.com/mcp-hangar/mcp-hangar/issues/152)
* **docs:** provider to mcp_server terminology cleanup in cookbook ([#147](https://github.com/mcp-hangar/mcp-hangar/issues/147)) ([e6d0d1b](https://github.com/mcp-hangar/mcp-hangar/commit/e6d0d1b95601560329f099c878c783bbf97084bc)), closes [#129](https://github.com/mcp-hangar/mcp-hangar/issues/129)
* **docs:** replace broken prerequisites with in-repo provider_math image ([#142](https://github.com/mcp-hangar/mcp-hangar/issues/142)) ([d318671](https://github.com/mcp-hangar/mcp-hangar/commit/d3186714ee3159a8138c2055e2eed57c8da0f15b)), closes [#128](https://github.com/mcp-hangar/mcp-hangar/issues/128)
* **docs:** REST_API.md auth method, empty sections, discovery prereq ([#156](https://github.com/mcp-hangar/mcp-hangar/issues/156)) ([aa34274](https://github.com/mcp-hangar/mcp-hangar/commit/aa3427452c263cc1b53d2a0c91f8c06bf513083d)), closes [#153](https://github.com/mcp-hangar/mcp-hangar/issues/153)
* **observability:** restore set_tracer_provider call broken by global rename ([#150](https://github.com/mcp-hangar/mcp-hangar/issues/150)) ([41b5be9](https://github.com/mcp-hangar/mcp-hangar/commit/41b5be97219eb642f9fa40ceeb3c91ed6d2414c9))


### Security

* **ci:** scope dependabot-automerge pull_request_target to main ([#116](https://github.com/mcp-hangar/mcp-hangar/issues/116)) ([eb4b53b](https://github.com/mcp-hangar/mcp-hangar/commit/eb4b53b47e62b8cde4cd165aa38c4c85ddcdcbc9)), closes [#112](https://github.com/mcp-hangar/mcp-hangar/issues/112)

## [1.1.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.3...v1.1.0) (2026-05-10)


### Added

* **observability:** wire observability and compliance loop end-to-end ([#107](https://github.com/mcp-hangar/mcp-hangar/issues/107)) ([5dbdfc9](https://github.com/mcp-hangar/mcp-hangar/commit/5dbdfc9374283835fffdff05e1d4cd9217a86455)), closes [#106](https://github.com/mcp-hangar/mcp-hangar/issues/106)


### Fixed

* **ci:** bump actions/add-to-project from v1 to v2 ([#100](https://github.com/mcp-hangar/mcp-hangar/issues/100)) ([b248fa1](https://github.com/mcp-hangar/mcp-hangar/commit/b248fa152a09a9686141daa41f9d9dd6059ca699))
* **ci:** fix release notes %0A encoding, duplicate What's Changed, and HTML entities ([#109](https://github.com/mcp-hangar/mcp-hangar/issues/109)) ([7d2e691](https://github.com/mcp-hangar/mcp-hangar/commit/7d2e6915bf6e84265174c852754e01b1be731395))

## [1.0.3](https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.2...v1.0.3) (2026-05-10)


### Fixed

* add TTL for failover saga states to prevent ghost entries ([3757c3f](https://github.com/mcp-hangar/mcp-hangar/commit/3757c3f15f89caf8806a76281be4439375a7a676))
* **ci:** drop strict flag from pip-audit to allow skip-editable ([#93](https://github.com/mcp-hangar/mcp-hangar/issues/93)) ([2ca2d1c](https://github.com/mcp-hangar/mcp-hangar/commit/2ca2d1c2f67c8f1364cc1379cd1c7cc27d798b97))
* **ci:** fix crlf parsing and relax commitlint subject-case ([#72](https://github.com/mcp-hangar/mcp-hangar/issues/72)) ([664323f](https://github.com/mcp-hangar/mcp-hangar/commit/664323fd8f28caee705d3d4715daa3c6ba19f9c1))
* **ci:** skip editable installs in pip-audit to avoid self-lookup ([#85](https://github.com/mcp-hangar/mcp-hangar/issues/85)) ([e707862](https://github.com/mcp-hangar/mcp-hangar/commit/e707862b025d353c8678444d87f124567b0abc1f))
* **ci:** skip pr-title and commitlint on release-please branches ([#88](https://github.com/mcp-hangar/mcp-hangar/issues/88)) ([9045b50](https://github.com/mcp-hangar/mcp-hangar/commit/9045b50f1704c125e825144eba79da12ef736436))
* consolidate auth context storage to single canonical write path ([bc1d42b](https://github.com/mcp-hangar/mcp-hangar/commit/bc1d42b3a0431ad802b92b0d163b9e2851a12a20))
* **docs:** add blank lines before lists in ADR-004 and ADR-005 (MD032) ([d2f046b](https://github.com/mcp-hangar/mcp-hangar/commit/d2f046b3f1668afe78f8fade1c64cd6b1e497a59))
* **docs:** resolve markdownlint MD032 and mkdocs strict-mode link warnings ([494a4cd](https://github.com/mcp-hangar/mcp-hangar/commit/494a4cd4f8ab261a482b70d10ff81cfb15a7b846))
* **docs:** use glob exclusion for changelog in markdownlint workflow ([#92](https://github.com/mcp-hangar/mcp-hangar/issues/92)) ([56886a6](https://github.com/mcp-hangar/mcp-hangar/commit/56886a63b9c38f8a76ba0e552d0b20edf2fedb3d))
* restore TracerProvider import broken by Provider-to-McpServer rename ([fcb204a](https://github.com/mcp-hangar/mcp-hangar/commit/fcb204a4d5861901b55b64fbf3aef41bcc0e2bb9))
* **tests:** align test assertions with McpServerMode enum and tools.list_names() API ([b67b5c3](https://github.com/mcp-hangar/mcp-hangar/commit/b67b5c3ac0f770497f4e2d45333dcf51fe2c3e3b))


### Changed

* clean up Provider -&gt; McpServer legacy shims in events and commands ([e4aa6db](https://github.com/mcp-hangar/mcp-hangar/commit/e4aa6dbbbd179469b7eb095935226c2a57e9a77e))
* eliminate all static enterprise imports in core (TASK-P0-2, TASK-PRECOMMIT-FIX) ([53c2b73](https://github.com/mcp-hangar/mcp-hangar/commit/53c2b73780dd818263204e8923ef8f518e6be52a))
* reuse thread-local event loop in approval gate instead of creating per call ([a7a4338](https://github.com/mcp-hangar/mcp-hangar/commit/a7a4338e3314e469195e1f0d62a5c17899531f9e))

## [1.0.2] - 2026-04-24

### Changed

- **Enterprise Boundary**: server bootstrap and router code now resolve optional enterprise integrations through a single core provider boundary in `server/bootstrap/enterprise.py` instead of scattered direct `enterprise.*` imports. The boundary supports entry-point discovery when available and a monorepo-safe fallback for local development.
- **Unified Auth Enforcement**: HTTP and WebSocket auth now share the same core enforcement path in `server/api/middleware.py`, including trusted proxy resolution, `?token=` WebSocket bearer mapping, auth context propagation, and consistent 401/403/1008 failures.
- **CSRF Scope**: CSRF protection now targets browser-style session suspension requests instead of all mutating API routes. Browser hints (`Origin`, `Referer`, `Cookie`) plus `X-Requested-With` are used to distinguish SPA/browser requests from API clients.

### Security

- Browser-originated `POST /sessions/{id}/suspend` now requires `X-Requested-With`, while API key / bearer / non-browser clients remain unaffected.
- Direct server-layer `from enterprise` imports were removed from the bootstrap/router path, shrinking the core-to-enterprise attack surface and making the boundary auditable in one place.

### Added

- `tests/integration/test_e2e_mcp_flow.py` -- end-to-end integration tests for full MCP server lifecycle.
- `tests/security/test_identity_network.py` -- security regression tests for identity extraction and network hardening.

## [1.0.1] - 2026-04-17

Security hardening release addressing findings from the April 2026 security audit.

### Added

- **SSRF Protection**: Block remote MCP server endpoints resolving to private/link-local addresses (10.0.0.0/8, 127.0.0.0/8, 169.254.0.0/16, etc.)
- **Trusted Proxy Resolver**: `TrustedProxyResolver` with CIDR support, configurable via `MCP_TRUSTED_PROXIES` env var
- **Granular RBAC Permissions**: `policy:write`, `MCP servers:read`, `MCP servers:write`, `MCP servers:lifecycle`, `config:reload` permissions with `agent` role for hangar-agent tokens
- **Command Allow-List**: Default-deny `ALLOWED_COMMANDS` (node, python, docker, uv, etc.) replaces the old blocklist approach; configurable via `MCP_ALLOWED_COMMANDS` env var
- **WebSocket Origin Validation**: Validates `Origin` header against CORS config before accepting WebSocket connections
- **WebSocket Backpressure**: Per-connection bounded queue (maxsize=1024) with subscriber limit (max 100)
- **Domain Contracts**: `IProviderLauncher`, `LaunchResult`, `ILock` protocols for DDD boundary enforcement
- **ADR Documents**: ADR-001 (CQRS), ADR-002 (Event Sourcing), ADR-003 (Sagas)
- **Security Tests**: `tests/security/test_critical.py` and new unit tests for SSRF, trusted proxy, JWT extractor, RBAC, WS auth

### Changed

- **Launcher Architecture**: MCP Server launchers moved from `domain/services/mcp_server_launcher/` to `infrastructure/launchers/`; old paths are deprecation shims
- **Server State**: Eager globals replaced with lazy-initialized `server/bootstrap/composition.py`; `ProviderDict` wrapper removed
- **Enterprise Module Loading**: Uses `importlib.metadata` entry points instead of direct enterprise imports
- **CORS Defaults**: `allow_credentials=False`, explicit methods and headers instead of wildcards
- **Identity Extraction**: `IIdentityExtractor.extract()` now accepts `source_ip` for trusted proxy validation
- **Enterprise HTTP Middleware**: Unified request metadata extraction with core `IdentityMiddleware`
- **CloudConnector**: Replaced `hasattr()` checks with explicit `None` initialization and guards

### Security

- Command execution restricted to allow-list only (default-deny)
- SSRF validation on remote MCP server endpoint URLs
- Trusted proxy CIDR resolution prevents IP spoofing via `X-Forwarded-For`
- JWT algorithm confusion guard for mixed symmetric/asymmetric families
- WebSocket CSWSH protection via Origin validation

## [1.0.0] - 2026-04-11

First stable release. All public APIs are now covered by semantic versioning guarantees.

### Added

- **Enterprise Module System** (Phases 47, BSL 1.1):
  - `LicenseTier` enum (COMMUNITY, PRO, ENTERPRISE) with `LicenseValidator` HMAC-SHA256 key validation
  - `EnterpriseComponents` dataclass and `load_enterprise_modules()` bootstrap integration
  - License tier gating: enterprise features activate based on license key; all failure modes fall back to COMMUNITY
  - HMAC signing secret configurable via `HANGAR_LICENSE_HMAC_SECRET` environment variable (no longer hardcoded)

- **Capability Declaration and Enforcement** (Phases 38-41):
  - `McpServerCapabilities` value object with network, filesystem, environment, tool, and resource declarations
  - `from_dict()` factory and config.yaml integration for capability blocks
  - Kubernetes CRD types for capabilities with reconciler propagation to status
  - `NetworkPolicyBuilder` pure function generating Kubernetes NetworkPolicy from declared egress rules
  - Docker capabilities-aware network mode in `DockerLauncher`
  - `ViolationType` and `ViolationSeverity` enums with Prometheus violations counter
  - `ViolationRecord` CRD type and `ViolationDetected` condition in operator reconciler
  - CEL admission validation and `ExpectedTools` field in MCPServer CRD
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
  - MCP Server endpoints: list, detail, start, stop, tool invocation history
  - Group and discovery source management endpoints
  - Config and system status endpoints
  - Auth endpoints with API key and role management
  - Observability endpoints (audit log, alerts)
  - WebSocket infrastructure: `ws_events_endpoint`, `ws_state_endpoint`, connection manager with queue and filters
  - `EventBus.unsubscribe_from_all` for WebSocket lifecycle

- **MCP Server Log Streaming** (Phases 21-22):
  - `LogLine` value object, `IProviderLogBuffer` contract, and `ProviderLogBuffer` ring buffer
  - Live stderr-reader threads for subprocess and Docker MCP servers
  - `GET /api/mcp_servers/{id}/logs` REST endpoint with `lines` parameter
  - `LogStreamBroadcaster` and `/ws/MCP servers/{id}/logs` WebSocket endpoint

- **MCP Server/Group CRUD** (Phase 23):
  - MCP Server CRUD events, commands, and handlers (create, update, delete)
  - Group CRUD handlers with `McpServerGroup.update()` and `to_config_dict()`
  - Config serializer module for export/backup
  - MCP Server and group CRUD REST endpoints
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
  - State-dependent check intervals (healthy vs degraded MCP servers)

- **CommandBus Middleware Pipeline**: Extensible middleware support for cross-cutting concerns
  - `RateLimitMiddleware` wired into bootstrap for command-level rate limiting

- **Docker Discovery Resilience**: Reconnection with exponential backoff on Docker daemon failures

- **Property-Based Testing**: Hypothesis-powered state machine tests for MCP Server aggregate

- **PEP 561 Support**: `py.typed` marker for downstream type checking

### Fixed

- **Concurrency Safety**: `McpServerGroup` lock hierarchy violation (CONC-01) resolved
- **invoke_tool() Refresh**: Split into two-lock-cycle pattern (CONC-03) to avoid holding locks during I/O
- **ensure_ready()/_start()**: Restructured with `threading.Event` coordination for safer startup
- **Exception Hygiene**: All exception catches across domain, application, infrastructure, and server layers
  narrowed and annotated -- no more bare `except Exception` without justification
- **Type Safety**: Fixed mypy errors in `rate_limiter`, `gc`, and `docker_source`

### Changed

- Discovery pipeline now validates commands before MCP server registration
- `StdioClient` ordering invariant documented with regression tests

## [0.10.0] - 2026-03-01

### Added

- **Kubernetes Operator Controllers**:
  - `MCPServerGroupReconciler` with label selection and status aggregation
  - `MCPDiscoverySourceReconciler` with 4 discovery modes
  - envtest integration tests for both controllers

- **Helm Chart Maturity**: Test templates and NOTES.txt for both charts, version bump to 0.10.0

- **Documentation Content**:
  - Configuration Reference page
  - MCP Tools Reference page
  - MCP Server Groups Guide
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

- **Tool Access Filtering**: Config-driven tool visibility control per MCP server, group, or member
  - `ToolAccessPolicy` value object with fnmatch glob pattern support (`*`, `?`, `[seq]`)
  - `ToolsConfig` dataclass for YAML configuration with `allow_list` and `deny_list`
  - `ToolAccessResolver` domain service with 3-level policy merge (MCP server -> group -> member)
  - Caching with automatic invalidation on policy changes
  - `ToolAccessDeniedError` exception for filtered tools (does not leak policy details)
  - Integration with hot-loading (`LoadProviderCommand.allow_tools/deny_tools`)
  - Integration with config reload (policies cleared and re-registered)
  - New Prometheus metrics: `mcp_hangar_tool_access_denied_total`, `mcp_hangar_tool_access_policy_evaluations_total`
  - Example config:

    ```yaml
    mcp_servers:
      grafana:
        tools:
          deny_list:
            - delete_*
            - create_alert_rule
    ```

- **Container Command Override**: Docker/Podman MCP servers can now override container entrypoint
  - `container.command` — list of strings to override container entrypoint
  - `container.args` — additional arguments passed after command
  - Example config:

    ```yaml
    mcp_servers:
      custom:
        mode: docker
        image: my-mcp-server:latest
        container:
          command: ["python", "-m", "custom_entrypoint"]
          args: ["--verbose"]
    ```

### Changed

- `McpServerState` is now exported from `mcp_hangar.domain.model` module

## [0.7.0] - 2026-02-08

### Added

- **Facade `max_concurrency` config**: `HangarConfig.max_concurrency(n)` configures maximum parallel
  tool invocations through `Hangar.invoke()`. Default: 20, range: 1-100.
  - Also exposed in `HangarConfigData.max_concurrency` and `to_dict()` output
  - Constants `FACADE_DEFAULT_CONCURRENCY` (20) and `FACADE_MAX_CONCURRENCY` (100) exported from `facade` module
- **Two-level concurrency model**: New `ConcurrencyManager` with global and per-MCP server semaphores
  - Global semaphore limits total in-flight calls across all MCP servers and batches (default: 50)
  - Per-MCP server semaphores limit concurrent calls to each individual MCP server (default: 10)
  - Consistent lock ordering (global-first, then MCP server) prevents deadlocks
  - All calls submitted to thread pool at once — no more sequential chunking into waves
  - Calls start as soon as any slot is free, enabling true parallel execution
- **Concurrency configuration**: New `execution` section in `config.yaml`
  - `execution.max_concurrency` — global limit across all MCP servers
  - `execution.default_mcp_server_concurrency` — default per-MCP server limit
  - Per-MCP server `max_concurrency` override in MCP server config
- **Concurrency observability**: New Prometheus metrics for concurrency control
  - `mcp_hangar_batch_inflight_calls` — global in-flight call gauge
  - `mcp_hangar_batch_inflight_calls_per_mcp_server` — per-MCP server in-flight gauge
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
  true parallelism benefits of the MCP server architecture (e.g., 20 parallel 100ms calls took ~520ms
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
- **CLI add MCP server test**: Fixed assertion to accept both uvx and npx package names
  - Test now correctly validates `mcp-server-fetch` (uvx) or `@modelcontextprotocol/server-fetch` (npx)

## [0.6.6] - 2026-02-06

### Added

- **Cookbook Documentation**: Step-by-step production recipes for MCP Hangar
  - Recipe 01 — HTTP Gateway: Single MCP server behind Hangar as control plane
  - Recipe 02 — Health Checks: Automatic health monitoring with state transitions on failure
  - Recipe 03 — Circuit Breaker: MCP Server groups with circuit breaker for fast-fail protection
  - Recipe 04 — Failover: Automatic failover to backup MCP server with priority-based routing
  - All recipes include complete config, step-by-step Try It sections, and technical explanations
  - Recipes build on each other sequentially (01 → 02 → 03 → 04)
  - Each recipe validated with working configs and real Hangar tests
  - Located in `docs/cookbook/` with index and schema documentation

- **Hot-Reload Configuration**: Live configuration reloading without process restart
  - Automatic file watching via watchdog (inotify/fsevents) with polling fallback
  - SIGHUP signal handler for Unix-style reload
  - New MCP tool `hangar_reload_config` for interactive reload from AI assistant
  - Intelligent diff: only restarts MCP servers with changed configuration
  - Unchanged MCP servers preserve their state and active connections
  - Atomic reload: invalid configuration is rejected, current config preserved
  - New domain events: `ConfigurationReloadRequested`, `ConfigurationReloaded`, `ConfigurationReloadFailed`
  - New command: `ReloadConfigurationCommand` with CQRS handler
  - Background worker `ConfigReloadWorker` for automatic file monitoring
  - Configurable via `config_reload` section in config.yaml

- **Init Dependency Detection**: `mcp-hangar init` now detects available runtimes before offering MCP servers
  - Step 0 checks for `npx`, `uvx`, `docker`, `podman` in PATH
  - MCP servers filtered by available dependencies (npx-based MCP servers hidden when Node.js not installed)
  - Clear error message with install instructions when no runtimes found
  - Unavailable MCP servers shown grayed out with "(requires npx)" hint
  - Bundles automatically filtered to only include installable MCP servers
  - New module: `dependency_detector.py` with `DependencyStatus`, `detect_dependencies()`

- **Init Smoke Test**: `mcp-hangar init` now tests MCP servers after configuration
  - Step 5 starts each MCP server and waits for READY state (max 10s total)
  - Shows green checkmark per MCP server on success: `✓ filesystem ready (1234ms)`
  - Shows detailed error with actionable suggestion on failure
  - Summary shows pass/fail count before "Restart Claude Desktop" prompt
  - Skip with `--skip-test` flag if needed
  - New module: `smoke_test.py` with `run_smoke_test()`, `SmokeTestResult`

- **Init Existing Config Handling**: `mcp-hangar init` now handles existing configuration safely
  - Interactive mode prompts with three options: Merge, Backup & Overwrite, Abort
  - Merge: Adds new MCP servers while preserving existing ones (no overwrites)
  - Backup & Overwrite: Creates timestamped backup, then replaces with new config
  - Abort: Cancels init, preserves existing configuration unchanged
  - Non-interactive mode (`-y`): Always creates backup then overwrites
  - `--reset` flag: Overwrites without backup or prompt
  - Never silently overwrites existing configuration
  - New method: `ConfigFileManager.merge_mcp_servers()` for safe merging

- **Init uvx Support (Dual-Stack)**: `mcp-hangar init` now supports uvx as alternative to npx
  - MCP servers with Python equivalents can now run via uvx when Node.js not available
  - Runtime priority: uvx > npx (dogfooding - MCP Hangar is Python-based)
  - Mapping: `npx @modelcontextprotocol/server-fetch` -> `uvx mcp-server-fetch`
  - All starter MCP servers (filesystem, fetch, memory) have uvx packages
  - Config generates appropriate command based on detected runtimes
  - MCP Server unavailable only if NO suitable runtime available
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
  - MCP server state metrics: `mcp_hangar_mcp_server_state`, `mcp_hangar_mcp_server_up`, `mcp_hangar_mcp_server_starts_total`, `mcp_hangar_mcp_server_stops_total`
  - Tool call metrics: `mcp_hangar_tool_calls_total`, `mcp_hangar_tool_call_duration_seconds`, `mcp_hangar_tool_call_errors_total`
  - Health check metrics: `mcp_hangar_health_checks_total`, `mcp_hangar_health_check_duration_seconds`, `mcp_hangar_health_check_consecutive_failures`
  - Rate limiter metrics: `mcp_hangar_rate_limit_hits_total`
  - HTTP client metrics: `mcp_hangar_http_requests_total`, `mcp_hangar_http_request_duration_seconds`, `mcp_hangar_http_errors_total`
  - `MetricsEventHandler` bridges domain events to Prometheus
  - HTTP client instrumented with MCP server label support

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
  - Removed alerts referencing `mcp_server_state`, `mcp_server_up`, `discovery_*` (not yet populated)

### Documentation

- Complete rewrite of `docs/guides/OBSERVABILITY.md`
  - Documented "Currently Exported Metrics" vs "Metrics Not Yet Implemented"
  - Updated alert tables to match actual `alerts.yaml`
  - Fixed PromQL examples with correct metric names
  - Added production readiness checklist

### Added (Dashboards)

- New `alerts.json` Grafana dashboard for alert monitoring
- New `MCP server-details.json` Grafana dashboard for per-MCP server deep dive

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

- **MCP Server launcher modularization**: Split `domain/services/mcp_server_launcher.py` into package
  - `domain/services/mcp_server_launcher/__init__.py` - Public API
  - `domain/services/mcp_server_launcher/base.py` - Base launcher interface
  - `domain/services/mcp_server_launcher/subprocess.py` - Subprocess launcher
  - `domain/services/mcp_server_launcher/docker.py` - Docker launcher
  - `domain/services/mcp_server_launcher/container.py` - Container utilities
  - `domain/services/mcp_server_launcher/http.py` - HTTP/SSE launcher
  - `domain/services/mcp_server_launcher/factory.py` - Launcher factory

### Migration

If you have scripts or integrations using the old `registry_*` tool names, update them to use `hangar_*`:

```python
# Before
registry_tools(mcp_server="math")
registry_health()

# After
hangar_tools(mcp_server="math")
hangar_health()
```

## [0.6.0] - 2026-01-31

### Added

- **Interactive CLI**: New typer-based CLI for streamlined MCP server management
  - `hangar init` - Initialize new project with guided setup
  - `hangar add <MCP server>` - Add MCP servers interactively with auto-configuration
  - `hangar remove <MCP server>` - Remove MCP servers from configuration
  - `hangar status` - Show current MCP servers and their states
  - `hangar serve` - Start the MCP server (default command)
  - `hangar completion` - Generate shell completion scripts
  - Rich console output with colors and progress indicators
  - JSON output mode for scripting (`--json`)
  - Backward compatible with existing argparse CLI

- **MCP Server Bundles**: Pre-configured MCP server definitions for quick setup
  - Built-in definitions for popular MCP servers (filesystem, memory, sqlite, fetch, github, slack, etc.)
  - `InstallType` enum: NPX, UVX, DOCKER, BINARY
  - `ConfigType` enum: NONE, PATH, SECRET, STRING, URL
  - Bundle resolver for discovering and validating MCP servers

- **Multi-runtime Installers**: Pluggable installer infrastructure
  - `NpmInstaller` - Install MCP servers via npx
  - `PyPIInstaller` - Install MCP servers via uvx
  - `OCIInstaller` - Pull and run Docker/OCI images
  - `BinaryInstaller` - Download and execute pre-built binaries
  - Automatic runtime detection and validation

- **Package Resolver**: Unified package resolution across ecosystems
  - Resolve MCP server packages from npm, PyPI, or container registries
  - Version validation and compatibility checks

- **Secrets Resolver**: Secure configuration management
  - Environment variable interpolation (`${VAR_NAME}`)
  - Support for secret references in MCP server configs
  - Integration with system keychain (future)

- **Output Redactor**: Automatic sensitive data redaction
  - Redact API keys, tokens, and passwords from logs
  - Configurable redaction patterns
  - Safe for production logging

- **Runtime Store**: Persistent storage for installed MCP server runtimes
  - Track installed MCP servers and their versions
  - Cache validation and cleanup

### Changed

- Refactored CLI into modular command structure under `server/cli/`
- Legacy CLI preserved in `cli_legacy.py` for backward compatibility
- MCP Server launcher now supports multiple install types

### Documentation

- Updated quickstart guide with new CLI commands

## [0.5.0] - 2026-01-29

### Added

- **Batch Invocations**: New `hangar_batch()` tool for parallel tool execution
  - Execute multiple tool invocations in a single API call
  - Configurable concurrency (1-20 parallel workers)
  - Single-flight pattern for cold starts (one MCP server starts once, not N times)
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
  - `mcp_hangar_batch_circuit_breaker_rejections_total{MCP server}` - CB rejections
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
| `mcp_registry_provider_state` | `mcp_hangar_mcp_server_state` |
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

- **Core**: Enhanced `McpServerStartError` with diagnostic information
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

- Updated troubleshooting guide with MCP server startup error diagnostics
- Added programmatic error handling examples

## [0.3.0] - 2026-01-21

### Added

- **Facade API**: New high-level `Hangar` class for simplified MCP server management
  - Async-first API with `await hangar.invoke()`, `await hangar.health()`
  - Sync wrapper `SyncHangar` for simple scripting use cases
  - Context manager support: `async with Hangar.from_config(...) as hangar:`
- **HangarConfig Builder**: Programmatic configuration with fluent API
  - `.add_mcp_server()` for subprocess, docker, and remote MCP servers
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

- **Core**: Improved error diagnostics for MCP server startup failures - stderr from container/subprocess is now included in error messages instead of generic "unknown error"
- **Core**: `StdioClient` now captures and propagates stderr to error messages when process dies
- **Core**: `MCP Server._handle_start_failure()` now receives actual exception instead of None

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
  - Built-in roles: admin, mcp_server_admin, developer, viewer, auditor
  - Permission-based authorization (MCP server:*, tool:invoke, etc.)
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

- **MCPServer CRD**: Declarative MCP server management
  - Container and remote MCP server modes
  - Configurable health checks and circuit breaker
  - Resource limits and security contexts
  - Environment variables from Secrets/ConfigMaps
  - Volume mounts (Secret, ConfigMap, PVC)

- **MCPServerGroup CRD**: High availability
  - Label selector-based MCP server grouping
  - Load balancing strategies (RoundRobin, LeastConnections, Random, Failover)
  - Configurable failover with retries
  - Health policy enforcement

- **MCPDiscoverySource CRD**: Auto-discovery
  - Namespace-based discovery
  - ConfigMap-based discovery
  - Additive and Authoritative modes
  - MCP Server templates for defaults

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
  - Span nesting for tool invocations and MCP server operations
  - Automatic score recording for health checks and success rates
  - Graceful degradation when Langfuse is unavailable
  - Configuration via environment variables or config file

- **Testcontainers Support**: Production-grade integration testing
  - PostgreSQL, Redis, Prometheus, Langfuse container fixtures
  - Custom MCP server container fixtures
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

- **MCP Server Groups**: Load balancing and high availability for multiple MCP servers
  - Group multiple MCP servers of the same type into a single logical unit
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
- Hot-loading MCP server management with automatic lifecycle control
- Multiple transport modes: Stdio (default) and HTTP with Streamable HTTP support
- Container support for Docker and Podman with auto-detection
- Pre-built image support for running any Docker/Podman image directly
- Thread-safe operations with proper locking mechanisms
- Health monitoring with active health checks and circuit breaker pattern
- Automatic garbage collection for idle MCP server shutdown
- MCP server state machine: `COLD → INITIALIZING → READY → DEGRADED → DEAD`
- Registry MCP tools: `registry_list`, `registry_start`, `registry_stop`, `registry_invoke`, `registry_tools`, `registry_details`, `registry_health`
- Comprehensive security features:
  - Input validation at API boundaries
  - Command injection prevention
  - Rate limiting with token bucket algorithm
  - Secrets management with automatic masking
  - Security audit logging
- Domain-Driven Design architecture with CQRS pattern
- Event sourcing support for MCP server state management
- Subprocess mode for local MCP server processes
- Container mode with security hardening (dropped capabilities, read-only filesystem, no-new-privileges)
- Volume mount support with blocked sensitive paths
- Resource limits (memory, CPU) for container MCP servers
- Network isolation options (none, bridge, host)
- Example math MCP server for testing
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

- Input validation for all MCP server IDs, tool names, and arguments
- Command sanitization to prevent shell injection attacks
- Environment variable filtering to remove sensitive data
- Rate limiting to prevent denial of service
- Audit logging for security-relevant events

[Unreleased]: https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.12.0...v1.0.0
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
[0.2.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mcp-hangar/mcp-hangar/releases/tag/v0.1.0

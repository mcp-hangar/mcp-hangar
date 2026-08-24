# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.14.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.13.1...v2.14.0) (2026-08-24)

Hangar's side of SEP-2243. A tool whose `x-mcp-header` annotations are invalid
is no longer projected -- a conforming client drops it on arrival, so
advertising it handed out a tool nobody could call -- and a new
`header_exposure` block governs which parameters an upstream may oblige a client
to send as an HTTP header, the enforcement point behind a SHOULD NOT the spec
leaves unbacked. An egress policy can select on `Mcp-Param-*`, and a request on
a revision that predates mandatory header-body validation never satisfies such a
selector.

Doing that work surfaced eight metrics that were defined, incremented on the
live path, and **never registered**, so they were absent from every scrape --
including the three approval-gate counters the observability guide documents
queries against, dead since 2.10.0. They are on `/metrics` now, and a test walks
the metrics module so the next one cannot be forgotten.

### Added

- **core:** an `MCPEgressPolicy` can now select on SEP-2243 `Mcp-Param-*` headers
  (`headers.allow` / `deny` / `requireApproval`, same glob precedence as the
  tool-name rules), so region, tenant and priority are enforceable without
  parsing the body. A request whose `MCP-Protocol-Version` predates mandatory
  header-body validation never satisfies such a selector: nothing has checked
  that its headers agree with its body, so the tool rules and the policy default
  decide instead. Only `Mcp-Param-*` names are selectable -- a selector on
  `Authorization` is refused at parse. ([#1064](https://github.com/mcp-hangar/mcp-hangar/pull/1064))
- **core:** a new per-server (or per-group) `header_exposure:` block governs which
  parameters an upstream may oblige a client to send as an HTTP header. SEP-2243
  lets a tool annotate an `inputSchema` property with `x-mcp-header`, and its only
  defence against annotating a secret is a SHOULD NOT -- so an upstream that
  annotates `api_key` puts the key in front of every intermediary on the path.
  `deny_annotated` globs are matched against both the annotation token and the
  property path; `on_violation` is `warn` (the default, which changes nobody's
  surface), `withdraw`, or `refuse_boot`. An unknown `on_violation` is refused at
  parse rather than resolving to the default. The schema is never edited, so
  digests and pins do not move. ([#1065](https://github.com/mcp-hangar/mcp-hangar/pull/1065))
- **core:** a tool whose `x-mcp-header` annotations are invalid is no longer
  projected through the front door. SEP-2243 makes dropping it a client-side
  MUST, so advertising it handed out a tool nobody could call. The definition is
  never edited -- stripping the annotation would move the JCS digest and read as
  upstream drift -- the tool is withheld instead, with a log line naming the
  reason and `mcp_hangar_projection_withdrawals_total{reason="invalid_x_mcp_header"}`
  counting it once per schema version. ([#1063](https://github.com/mcp-hangar/mcp-hangar/pull/1063))

### Fixed

- **core:** four metrics were defined, incremented on the live path, and absent
  from every `/metrics` scrape because nothing added them to the registration
  list: the three approval-gate counters (`mcp_hangar_approval_requests_total`,
  `_deliveries_total`, `_decisions_total`, dead since 2.10.0 — the three PromQL
  queries in the observability guide could never return a row) and
  `mcp_hangar_egress_policy_violations_observed_total`, the Audit-mode signal
  ADR-013 calls the safe adoption path for an egress policy. All four are
  registered, and a test now walks the metrics module so the next one cannot be
  forgotten. ([#1066](https://github.com/mcp-hangar/mcp-hangar/pull/1066))
- **core:** a `tools/call` whose `Mcp-Param-*` headers were never checked left no
  metric. Hangar now increments `mcp_hangar_param_header_validation_skipped_total`
  when the nested listing fails, omits the tool, or advertises an invalid
  `x-mcp-header`, and when a handshake-era call still carries `Mcp-Param-*`.
  The fail-open boundary itself is unchanged. ([#1060](https://github.com/mcp-hangar/mcp-hangar/pull/1060))
- **core:** a handshake-era `Mcp-Name` that used the SEP-2243 base64 sentinel was
  refused as a header/body mismatch, and a modern `tools/call` with arguments
  recounted `mcp_hangar_projected_tools` because the SDK's schema lookup re-ran
  `tools/list`. Routing headers now go through `decode_header_value`, a mismatch
  answers `-32020` (`HEADER_MISMATCH`) like `tasks/*`, and the identity-scoped
  projection is memoised for the lifetime of one HTTP request so the metric
  still measures listings a client actually received. ([#1060](https://github.com/mcp-hangar/mcp-hangar/pull/1060))

## [2.13.1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.13.0...v2.13.1) (2026-08-24)

### Fixed

- **core:** a `ui://` resource can now be allowlisted and consented to rather than
  only denied. SEP-1865 mandates a human decision before such a resource reaches a
  client webview, and the guard stated that mandate while both halves that satisfy
  it were missing: nothing built a `UiResourcePolicy`, so no tenant had an
  allowlist, and no consent gate was ever attached, so an allowlisted resource was
  refused for want of anyone to ask. The policies come from a new `ui_resources`
  config block, the gate is an adapter over the approval service wired at
  bootstrap, and both halves still fail closed on their own -- an unconfigured
  deployment denies every `ui://` resource exactly as before. Consent stays
  mandatory: the file cannot turn it off (ADR-024) ([#1055](https://github.com/mcp-hangar/mcp-hangar/pull/1055))
- **core:** the startup reachability check no longer demands an approval gate for a
  policy no gate can serve. It read `approval_list` off every registered policy,
  including the prompt and resource kinds added in 2.13.0, and refused the boot over
  it -- so one configuration was fail-open at request time and fail-closed at boot
  at the same time. `iter_registered_policies()` takes a `kind` filter and both the
  gate and the delivery-channel checks ask for tools ([#1046](https://github.com/mcp-hangar/mcp-hangar/pull/1046))
- **core:** `approval_list` under `access.prompt` / `access.resource` is refused at
  load instead of accepted and ignored. It was documented as inherited from the
  tools policy and announced that way in 2.13.0, but `requires_approval()` has one
  consumer -- the tool call path -- so an approval-listed prompt or resource was
  listed and served immediately: no hold, no human, no metric. A configuration that
  asks for enforcement no path performs is now refused where it is written, the way
  per-tenant pins without an identity are. Whether the hold belongs on
  `resources/read` / `prompts/get` at all is a separate decision, so the refusal
  says "not supported", not "invalid" ([#1046](https://github.com/mcp-hangar/mcp-hangar/pull/1046))
- **core:** a call routed to a group is checked against the withdrawal gate and its
  digest pin. Both looked the tool up under the group id while the projection
  registry is keyed by the member that started, so the lookup returned `None` --
  "unknown tool, do not block" -- and a pinned tool served through a group was never
  validated against its pin, in either topology and with no listing filter behind
  it. The group id is asked first, the selected member answers otherwise ([#1046](https://github.com/mcp-hangar/mcp-hangar/pull/1046))
- **core:** the post-approval-hold re-check asks the resolver the same question the
  pre-hold gate asked. It re-resolved the effective policy with neither the target
  group nor the caller's tenant, although both were in scope: in `front_door` that
  is the fail-closed missing-identity branch, so **every** human-approved call was
  refused at dispatch with `Approval no longer valid at dispatch: tool is no longer
  allowed by policy`, and in `egress` a deny added to a group's policy during the
  hold -- the race this re-check exists to close -- was not seen ([#1046](https://github.com/mcp-hangar/mcp-hangar/pull/1046))
- **core:** a `tool_projection:` block on a **group** is read instead of silently
  dropped. Only the mcp_server branch parsed it, so a group could declare neither a
  withdrawal, a digest pin nor a `digest_enforcement` mode -- the key loaded without
  a warning and did nothing, which left the group with no id under which those
  controls could be both declared and read ([#1046](https://github.com/mcp-hangar/mcp-hangar/pull/1046))
- **core:** a prompt or resource withdrawn on a group MEMBER is now hidden for the
  whole group. The prompts and resources surfaces ask about a group under its group
  id, and the withdrawal overlay is keyed by the id it was declared under, so a
  member's `withdrawn_prompts` / `withdrawn_resources` was invisible to them. The
  union is fail-closed: members of one group are interchangeable, so an item
  withdrawn on one of two identical backends is not a state an operator can have
  meant ([#1046](https://github.com/mcp-hangar/mcp-hangar/pull/1046))
- **core:** a group's `access.prompt` / `access.resource` policy was registered and
  never read, so a declared deny enforced nothing on the prompts and resources
  surfaces (fail-open). `prompt_proxy._upstream_ids` collapses a group member to
  its group id before any check runs, and `is_governed_allowed` only mapped the
  other direction -- member id to group -- so it asked the resolver with
  `group_id=None` and the group's policy was never merged. Both spellings now
  resolve to the group scope, the way `tools:` on the same group always did ([#1046](https://github.com/mcp-hangar/mcp-hangar/pull/1046))

## [2.13.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.12.0...v2.13.0) (2026-08-20)

### Added

- **core:** prompts and resources are governed. Both surfaces shipped ungoverned
  within the tenant boundary; they now go through the *same* policy surface tools
  use, re-keyed `(mcp_server, kind, name)` rather than grown a second time as
  parallel `PromptAccessPolicy` / `ResourceAccessPolicy` objects. One resolver
  chokepoint, so a listing and a fetch cannot drift apart, and prompts and
  resources inherit the merge semantics, the approval gate, the per-tenant
  overlays and the fail-closed front-door branch instead of a weaker copy of each.

  New config, alongside the existing `tools:` block on an mcp_server or a group
  (and inside a `tool_access.member.<tenant>` entry):

  ```yaml
  access:
    prompt:   {deny_list: ["draft_*"]}
    resource: {allow_list: ["docs://*"]}
  tool_projection:
    withdrawn_prompts: [retired_prompt]
    withdrawn_resources: ["demo://gone/1"]
  ```

  `allow_list` / `deny_list` / `approval_list` mean exactly what they mean for
  tools. Enforcement lands at both ends of every surface -- `prompts/list` +
  `prompts/get`, `resources/list` + `resources/templates/list` + `resources/read`,
  and the handed-out `resource_link` catalogue -- so a denied item is absent from
  the listing AND refused on fetch, with the refusal indistinguishable from the
  one a nonexistent item gets. A resource is matched by its **upstream** uri
  (`demo://doc/1`), not the `hangar://<upstream>/…` projection of it: the upstream
  form is the stable identity an operator writes, and the owning server is already
  the policy scope.

  Backward compatible by construction: every entry point defaults to
  `kind: tool`, so a config written before this parses and decides identically and
  governs tools only. The SEP-1865 `ui://` guard becomes a *case* of this surface
  rather than a mechanism beside it -- it is the first gate on the resource path,
  so an un-allowlisted `ui://` resource is now absent from the catalogue as well
  as unreadable, and no resource policy, however permissive, can open it ([#1032](https://github.com/mcp-hangar/mcp-hangar/pull/1032))
- **core:** `front_door` now serves an upstream's resources, not just the
  `resource_link`s it handed out. `resources/list` and `resources/templates/list`
  aggregate live across the tenant's own projected upstreams (the same per-tenant
  scoping as the prompts proxy), and `resources/read` reaches anything in that
  catalogue — still through `relay_request` and still behind the fail-closed
  `ui://` guard (SEP-1865).

  Because a resource URI does not say which upstream owns it, and two upstreams
  may legitimately serve the same one, **every URI the gateway hands out is now
  namespaced** as `hangar://<upstream id>/<the upstream's own URI>` and translated
  back on `resources/read`. The rewrite is unconditional, so a URI does not change
  shape when an unrelated upstream appears, and it is applied wherever an upstream
  payload crosses the front door: `resource_link` and embedded `resource` blocks
  in tool results, prompt results and relayed task results, plus the `contents` of
  a `resources/read` answer. Nothing is dropped on collision — two upstreams
  serving `demo://doc/1` both stay listed, under distinct projected URIs. Clients
  that captured a `resource_link` from 2.12.0 will see the new shape; the links
  are per-replica and in-memory, so an upgrade re-issues them either way. ([#1031](https://github.com/mcp-hangar/mcp-hangar/pull/1031))
- **core:** an upstream's prompts are served through the front door. In
  `front_door` mode `prompts/list` aggregates prompts per tenant across the
  tenant's own projected upstreams (flat naming per the tool convention: bare
  name, cross-server collisions drop both entries) and `prompts/get` relays to
  the owning upstream, so the `prompts` capability is advertised exactly when
  the proxy is active (#888 honesty rule preserved). MVP boundaries: no
  prompt-level policy yet (anything from the tenant's own upstreams is allowed,
  never another tenant's -- the governance seam is #1028) and no
  `completion/complete` (#1026) ([#1029](https://github.com/mcp-hangar/mcp-hangar/pull/1029))

### Fixed

- **core:** hot-unloading an mcp_server now retires its prompt and resource
  policies too, not only its tool policy. Since the policy surface became
  kind-keyed, `remove_mcp_server_policy` dropped one kind and left the other two
  registered for an id that is free to be loaded again -- so a server taking that
  id later would have been governed by its predecessor's prompt/resource rules,
  in either direction: a stale `deny_list` restricting a server that never
  declared one, or a stale `allow_list` filtering its catalogue. Unloading a
  server retires the whole server. ([#1034](https://github.com/mcp-hangar/mcp-hangar/pull/1034))

## [2.12.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.11.0...v2.12.0) (2026-08-18)

### Added

- **core:** a `resource_link` the front door hands out is now resolvable on the
  same gateway. Each relayed link is remembered per tenant (capability-style: a
  reference handed to tenant A is unknown to tenant B), `resources/read`
  forwards to the owning upstream, `resources/list` answers with the caller's
  handed-out links, and `ui://` resources go through the fail-closed SEP-1865
  guard (denied until an operator wires a policy). Before this the gateway
  proxied the reference faithfully and then answered `Unknown resource` when
  the client followed it. The full prompts/resources proxy (#889 -- upstream
  catalogues, templates, subscriptions, completions) remains open ([#1021](https://github.com/mcp-hangar/mcp-hangar/pull/1021))
- **core:** the gateway now opens and holds the standing `GET` stream of a
  remote (Streamable HTTP) upstream, so server-initiated messages finally have
  somewhere to land. `notifications/tools/list_changed` triggers rediscovery --
  a changed upstream catalogue no longer persists stale until the next restart.
  The upstream's MCP-protocol log notifications are deliberately not routed
  (SEP-2577 deprecates the Logging surface). An upstream that answers the
  `GET` with 404/405 simply has no
  channel; that is detected once and left alone. On shutdown, a legacy
  session-based upstream's negotiated session is now terminated with a `DELETE`
  instead of being abandoned to its server-side timer. Progress-token
  translation (#883) rides this channel and ships separately ([#1019](https://github.com/mcp-hangar/mcp-hangar/pull/1019))

### Changed

- **core:** `truncation.cache_driver: redis` fails closed. A missing `redis`
  package, an unparseable URL, or a server that cannot `SETEX` (a Sentinel
  listen port answers PING and fails every data command) used to fall back to
  the per-replica memory cache while the log still said `cache_driver=redis` --
  so cross-replica continuation fetches missed and nobody was told. Now: init
  failures refuse the boot, the constructor probes with `SETEX` (not PING), the
  boot log names the ACTUAL backend, and a truncated response only advertises a
  `continuation_id` when the full payload was stored. A new `redis` extra
  (`pip install mcp-hangar[redis]`) ships in the published image next to
  `[postgres]`; `cache_driver: memory` on a coordinated deploy stays legal and
  logs a per-replica warning. See UPGRADE.md ([#1022](https://github.com/mcp-hangar/mcp-hangar/pull/1022))

### Fixed

- **core:** the config schema drifted from the readers in both directions.
  A documented per-server `max_concurrency` warned as `unknown_config_key`,
  failed `mcp-hangar config check`, and was refused under
  `HANGAR_CONFIG_STRICT=1` even though the limit demonstrably applied; it is
  now in `SERVER_SPEC_KEYS`. `working_dir` sat in the schema with no reader --
  a config carrying it validated cleanly while the key silently did nothing --
  and is now rejected like any other unread key ([#1013](https://github.com/mcp-hangar/mcp-hangar/pull/1013))
- **core:** a misspelt discovery `mode` over REST answered 500 instead of 400.
  `POST /api/discovery/sources` and `PUT /api/discovery/sources/{source_id}`
  checked only that `mode` was present; a value like `"Authoritative"` reached
  the command handler's `DiscoveryMode(...)` conversion, whose bare `ValueError`
  was reported as "an internal server error occurred" and logged as an unhandled
  exception. Both endpoints now answer 400 naming the rejected value and the two
  valid spellings (`additive`, `authoritative`). If you scripted around the 500,
  read the 400's `detail` instead. ([#1016](https://github.com/mcp-hangar/mcp-hangar/pull/1016))
- **core:** a caller's `_meta.progressToken` on a `tools/call` is now relayed:
  the upstream is asked for progress with a per-call minted token, and its
  `notifications/progress` (arriving on the standing GET stream) are translated
  back to the caller's token on the caller's session. Before this the upstream
  was never asked, so every long call looked frozen to the caller who had bound
  a progress callback. The front-door call handler also no longer blocks the
  event loop for the duration of an upstream call -- concurrent requests on the
  same connection (including the progress notifications themselves) proceed
  while a call is in flight ([#1020](https://github.com/mcp-hangar/mcp-hangar/pull/1020))
- **core:** a group behind `tool_access.mode: front_door` collided with itself
  and served none of its tools. The flat projection keyed on the bare tool name
  and dropped both entries on a collision -- and group members expose the same
  names by definition. Members of one group now collapse into a single logical
  server: the projection lists each shared tool once, policy is checked against
  the group (the same check the call path applies), and calls dispatch through
  the group id so member selection stays with the group's strategy (round-robin,
  canary, health). Collisions across *different* backends are still dropped ([#1018](https://github.com/mcp-hangar/mcp-hangar/pull/1018))

## [2.11.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.10.1...v2.11.0) (2026-08-18)

### Added

- `requireApproval` in an MCPEgressPolicy now routes matching tool calls into
  the existing approval gate instead of failing closed as a slower deny. The
  invoke path consults the L7 verdict alongside the MRTR tool-access policy,
  blocks on `ApprovalGateService` (typed pending approval, `approval:resolve`
  chokepoint, dispatch-time revalidation), and only a granted approval converts
  the verdict -- deny still wins if the policy hardens during the hold, `Audit`
  mode never asks a human, and a deployment with no approval channel stays
  fail-closed exactly as before. ([#1001](https://github.com/mcp-hangar/mcp-hangar/pull/1001))

### Removed

- The #969 sweep: nine verified-unused surfaces left over from the factory cut
  are deleted from `src/` -- the `HangarError`/`Rich*` error zoo with its
  factories and `ErrorClassifier` (`is_retryable` stays), `ProgressTracker`,
  the `HealthEndpoint` registry nothing served (event-store durability get/set
  stays), `domain/bundles`, `AuditService`, the tenant/catalog/package
  exception cluster with `McpServerEntry`/`CatalogItemId`,
  `HangarLoadResult`/`HangarUnloadResult` and the unused REST serializers, the
  never-called metrics helpers (`init_metrics`, `timed`, `record_*` for
  unshipped detection features), and `initialize_runtime`/`shutdown_runtime`
  plus the `trace_tool_invocation` decorator. None had a production caller;
  see UPGRADE.md for the replacement surfaces. ([#1002](https://github.com/mcp-hangar/mcp-hangar/pull/1002))

### Fixed

- A browser CORS preflight is answered by the CORS layer instead of 401ing at
  authentication. `OPTIONS` hit `AuthEnforcementMiddleware` before any
  CORS middleware could speak -- with no `Access-Control-Allow-Origin` on the
  refusal -- so a browser OAuth client could not call `/mcp` or `/api/*` at
  all, allowed origin or not. Auth now skips `OPTIONS` (a preflight carries no
  credentials by design, matching the authorization chokepoint), and
  CORSMiddleware wraps the served combined app, which also gives `/mcp` CORS
  headers for the first time. Refused requests carry the CORS header too, so a
  browser can at least read the 401. ([#999](https://github.com/mcp-hangar/mcp-hangar/pull/999))

## [2.10.1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.10.0...v2.10.1) (2026-08-17)

### Security

- A Bearer JWT with `alg=none` (or any token whose signing key cannot be
  resolved from the configured JWKS) is now rejected with a clean 401
  `authentication_failed`, like every other invalid credential. Previously
  `PyJWKClientError` escaped the JWT validator -- it is not an
  `InvalidTokenError` subclass -- and surfaced as a raw 500, so a crafted
  unsigned token produced an internal error where garbage Bearer produced 401. ([#996](https://github.com/mcp-hangar/mcp-hangar/pull/996))
- A compiled L7 egress policy now survives restarts and reaches every replica.
  It was held only in the RAM of the replica that handled the operator's POST:
  in HA the other replicas ran denied tools and a rolling restart dropped
  enforcement everywhere, while the CR reported `Compiled`/`BackstopApplied`.
  The policy is persisted on the fleet snapshot (the `enforce_ssrf` precedent),
  restored with the row on startup and registration, and propagated live to
  peers through the event tail. `GET /api/mcp_servers/{id}/l7_policy` (new,
  `policy:read`) returns the attached policy or 404 -- previously the route had
  no GET at all, so delivery could not be verified. ([#997](https://github.com/mcp-hangar/mcp-hangar/pull/997))

## [2.10.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.9.0...v2.10.0) (2026-08-16)

### Added

- **core:** Hangar is published in the Official MCP Registry as
  `io.mcp-hangar/hangar`. A `server.json` at the repository root describes the
  PyPI distribution started over stdio -- `mcp-hangar` with no arguments -- and
  nothing else: there is no hosted instance, so the entry declares no `remotes`.
  Both of its version fields track `pyproject.toml` through release-please, and a
  `publish-registry` job in the release workflow publishes the entry after the
  PyPI upload for that tag exists, since the registry proves ownership by reading
  the `mcp-name:` marker out of the README that PyPI serves for exactly that
  version. Stable releases only: PyPI serves a prerelease under its PEP 440
  spelling, which is not the spelling `server.json` carries ([#989](https://github.com/mcp-hangar/mcp-hangar/pull/989))
- **core:** `config.yaml` now says something about a key nothing reads. It had no
  schema, so unknown keys were kept and ignored at every level: `commandd:
  [python]` built a subprocess server with no command, `idle_tt1_s: 60` applied
  nothing, and `auth: {enabledd: true}` was a deployment that believed it had
  enabled authentication. The failure surfaced later and elsewhere -- a subprocess
  that will not start reads like a broken server, not a misspelled key. Top-level
  section names, each section's own keys and `mcp_servers.<id>` spec keys are now
  checked, and the message names the offending key and the allowed set, matching
  what `domain/policies/dsl.py` already did for the policy DSL. This release
  **warns**; `HANGAR_CONFIG_STRICT=1` refuses now and refusal becomes the default
  in 3.0.0. New `mcp-hangar config check [path]` answers the same question without
  starting a gateway, exiting 1 on an unknown key ([#984](https://github.com/mcp-hangar/mcp-hangar/pull/984))
- **core:** `hangar_load` accepts `approval_tools`, so a server registered at
  runtime can put a tool behind human approval — the third outcome the YAML
  `tools:` surface already had. A load that asks for approval on a deployment
  with no approval gate is refused rather than registering a policy nothing
  enforces. ([#988](https://github.com/mcp-hangar/mcp-hangar/pull/988))

### Changed

- **infra:** an upgrade note now gets the version it shipped in. `UPGRADE.md`
  collects `## Next — ...` sections at PR time, next to the change that motivates
  them, and nothing gave them a number: eight accumulated while 2.7.0, 2.8.0 and
  2.9.0 shipped, so the changelog entries for those releases sent a reader to a
  section headed "Next" -- which reads the same before and after the release it
  describes. `assemble_release_changelog.sh` now folds them into one
  `## Upgrade to X.Y.Z` section in the same commit as the changelog assembly, so
  the release PR is also where a reviewer sees them together. That matters:
  drafts written against different PRs contradict each other once they land in
  one release, which is what the `builder()` note did. The 2.7.0-2.9.0 sections
  are backfilled from the published guide ([#986](https://github.com/mcp-hangar/mcp-hangar/pull/986))

### Fixed

- **core:** the flat tool projection can now be imported as the first Hangar module; its batch executor dependency is loaded when a call is dispatched, after bootstrap has finished wiring the serving surface ([#923](https://github.com/mcp-hangar/mcp-hangar/pull/923))
- **core:** discovery source configuration now refuses an unknown `mode` instead
  of silently treating it as `additive`. Correct misspelled values to
  `additive` or `authoritative` before upgrading. ([#924](https://github.com/mcp-hangar/mcp-hangar/pull/924))

## [2.9.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.8.0...v2.9.0) (2026-08-16)

### Removed

- **core:** `MCPServerFactory`, `HangarFunctions`, `ServerConfig` and the 13 `Hangar*Fn` protocols are removed from `mcp_hangar.fastmcp_server`. No shipped code constructed any of them: `serve --http` builds its MCP server in `server/bootstrap` and its ASGI app in `server/lifecycle.mcp_app_for_serving`. Keeping a second, uncalled construction path is what made #592, #594, #595 and #596 possible — a capability wired into it looked wired. `HANGAR_SERVER_NAME` stays. See `UPGRADE.md` ([#965](https://github.com/mcp-hangar/mcp-hangar/pull/965))
- **core:** `create_health_routes`, `create_combined_asgi_app`, `create_auth_combined_app` and `MCPServerFactory.create_asgi_app` are removed from `mcp_hangar.fastmcp_server`. They were the factory's own ASGI assembly, which the shipped gateway never used — `serve --http` builds its app in `server/lifecycle.mcp_app_for_serving` and wraps it with `create_auth_enforced_app`. The two had drifted: the deleted routes were flat `/health` and `/ready`, while a running Hangar serves `/health/live`, `/health/ready` and `/health/startup`. Nothing about the served app changes; see `UPGRADE.md` ([#964](https://github.com/mcp-hangar/mcp-hangar/pull/964))
- **core:** `MCPServerFactoryBuilder` and `MCPServerFactory.builder()` are removed from `mcp_hangar.fastmcp_server`. The fluent builder had no caller in the shipped gateway — `serve --http` builds its app through `server/bootstrap` and `mcp_app_for_serving`, never the factory. Construct `MCPServerFactory(HangarFunctions(...))` directly; see `UPGRADE.md` ([#963](https://github.com/mcp-hangar/mcp-hangar/pull/963))

### Fixed

- **infra:** the `quickstart` and `otel-collector` examples now start. Both hit the auth guard (`Refusing to start HTTP on non-loopback without authentication`) and exited 1, both ignored the config they mounted because nothing set `MCP_CONFIG`, and all three compose examples ran their healthcheck through `curl`, which the `python:3.14-slim` image does not carry — against `/health`, which is a 404. `examples/**` has no CI, which is why none of it was noticed ([#966](https://github.com/mcp-hangar/mcp-hangar/pull/966))
- **core:** `hangar_load` can now succeed. Hot-loading is enabled by default, but bootstrap built its resolver with every runtime hardcoded unavailable and its installer list empty, so every load answered `"No compatible package found (missing runtime?)"` with `"Available runtimes: []"` — for every server, since the feature shipped. There are now `uvx` (pypi) and `npx` (npm) installers, and runtime availability is read from them rather than hardcoded. `oci` and `mcpb` remain unimplemented and are reported unavailable rather than selected and then dropped. Note the runtimes must be on the gateway's PATH: the published container image carries neither, so hot-loading there still fails — now with a message naming what is missing ([#961](https://github.com/mcp-hangar/mcp-hangar/pull/961))

## [2.8.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.7.0...v2.8.0) (2026-08-15)

### Changed

- **infra:** the published container now runs Python 3.14. `pip install` still supports 3.11–3.14; 3.14 is a required CI citizen, not advisory ([#933](https://github.com/mcp-hangar/mcp-hangar/pull/933))

### Removed

- **core:** `LogAuditStore` is removed from `mcp_hangar.application.event_handlers`. It was never constructed outside this repository's tests, and its `query()` raised `NotImplementedError` — a log sink cannot answer a query. `AuditStore`, `InMemoryAuditStore` and the OTLP exporter path are unchanged; see `UPGRADE.md` ([#951](https://github.com/mcp-hangar/mcp-hangar/pull/951))
- **core:** `CallbackAlertSink` is removed from `mcp_hangar.application.event_handlers`. Production `get_alert_handler()` builds a `LogAlertSink`; the callback wrapper was constructed only by this repository's tests, which now define their own capturing sink. `AlertSink`, `Alert`, `LogAlertSink` and `AlertEventHandler` are unchanged; see `UPGRADE.md` ([#959](https://github.com/mcp-hangar/mcp-hangar/pull/959))
- **core:** `detect_runtime_availability` and `IRuntimeChecker` are removed from `mcp_hangar.application.services`. Neither had a caller — hot-loading builds a `RuntimeAvailability` directly. `PackageResolver` and `RuntimeAvailability` are unchanged; see `UPGRADE.md` ([#952](https://github.com/mcp-hangar/mcp-hangar/pull/952))
- **core:** the `containers` extra is gone. It installed `testcontainers` for a test
  tier that never ran: those tests were gated behind `--run-containers` /
  `--run-slow`, and no CI job, `Makefile` target or script ever passed either flag,
  so every one of them reported `skipped` on every run. `pip install
  mcp-hangar[containers]` now fails -- there is nothing left for it to install, and
  nothing in the shipped package ever imported it. Tests that need a real runtime
  belong in the nightly `tests/live` tiers. ([#931](https://github.com/mcp-hangar/mcp-hangar/pull/931))
- **infra:** the bundled compose monitoring stack (`monitoring/`, `docker-compose.monitoring.yml`) is gone. The four Grafana dashboards and the 30 Prometheus alert rules ship with the Helm chart instead — `dashboards.enabled` renders them as sidecar-labelled ConfigMaps, `prometheusRule.enabled` renders a PrometheusRule. Instrumentation is untouched: `/metrics`, tracing and the OTLP exporter are unchanged; only bundled config was removed. There is no one-command local Grafana any more ([#936](https://github.com/mcp-hangar/mcp-hangar/pull/936))

## [2.7.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.6.0...v2.7.0) (2026-08-14)

### Added

- An approval gate that is armed but notifies nobody now says so at startup, and
  `approval_channel` finally selects a delivery.

  **The signal.** When a policy gates a tool and the channel that would notify for
  it reaches nothing outside the process — `noop`, or a vendor name no installed
  package claims — the startup check logs `subsystem_configured_but_unreachable`
  at ERROR, naming the scope and the channel. It does not refuse the boot: the
  gate is already fail-closed by timeout, so what is missing is a signal, not an
  enforcement, and refusing over a notification channel would turn a degraded
  notify path into an outage. A deployment that wants the stricter reading sets
  `approvals: {delivery: {required: true}}` and gets a refusal instead.

  Why it matters even though nothing leaks: every gated call hangs for
  `approval_timeout_seconds` and then errors, which from the outside looks like a
  broken gateway. The remediation reached for under that pressure is emptying
  `approval_list` — fail-closed in code, fail-open in the organisation.

  **`approval_channel` routes.** It was documented as the delivery channel for a
  policy's approvals, merged with care across scope narrowing, and dispatched
  nowhere: one global delivery handled every approval whichever policy raised it,
  so per-server channels were silently one channel. Approvals now go through the
  channel their policy names, resolved on first use so a policy arriving from a
  hot reload or over REST is routable too. An unset `approval_channel` — the
  default — means the deployment's `approvals.channel`, as before.

  **Metrics.** `mcp_hangar_approval_requests{channel}` against
  `mcp_hangar_approval_deliveries{channel,outcome}` (`sent`, `failed`,
  `not_notified`) and `mcp_hangar_approval_decisions{channel,decision}`
  (`granted`, `denied`, `expired`). Requests climbing while deliveries stay at
  zero is the armed-and-unmanned shape; `expired` climbing beside a flat `sent` is
  the same story from the other end. ([#920](https://github.com/mcp-hangar/mcp-hangar/pull/920))
- **core:** the upgrade note for 2.6.0. Two changes in that release can stop a
  deployment that works today -- a gateway with per-tenant digest pins and
  authentication off no longer starts, and the `hangar_*` tools now require the
  permission their REST equivalent has always required -- and both needed the
  before/after and the remedy written down rather than inferred from a changelog
  entry. Includes the tool-to-permission table with the built-in roles that hold
  each one, because the two combinations that surprise people (`provider-admin`
  cannot run lifecycle, `developer` cannot approve or read metrics) are not
  guessable from the role names ([#913](https://github.com/mcp-hangar/mcp-hangar/pull/913))

### Changed

- The built-in approval delivery channel is now called `event_stream`, because
  that is where an approval notification actually travels. It was called
  `dashboard`, after a management UI that shipped with the Hangar Cloud tier and
  was archived with it — a channel named after a product that no longer exists,
  whose `send()` wrote a log line and pushed to nothing, while the docstring
  claimed a WebSocket integration "wired via event bus" that was never wired.

  The push is real, just upstream of delivery: the gate publishes
  `ToolApprovalRequested` before it waits, and `/api/ws/events` streams every
  domain event, so any client holding `audit:read` sees a held call — id, tool,
  channel label, expiry — in real time. The channel is now named after that
  surface.

  `channel: dashboard` still resolves, to the same delivery, and logs
  `approval_delivery_channel_renamed` once at boot saying where the name went; its
  config block is still read. Nothing to change on upgrade. `approval_channel`
  defaults to `event_stream` on new policies; existing approval records keep
  whatever label they were written with.

  Also removed: `hangar_approve_prompt`, a tool nothing registered, whose docstring
  pointed at an `approvals.channel: mcp_prompt` that no builtin or entry point has
  provided since 2.0. ([#916](https://github.com/mcp-hangar/mcp-hangar/pull/916))
- `serve --http` now serves the handshake-era MCP transport statelessly, so replicas
  of one gateway are one server to a client. A session lived in a single replica's
  memory, so a client that initialized against one pod and called against another was
  told `Session not found`; session affinity could not fix that, because a pin does not
  outlive its pod. `initialize` no longer returns an `Mcp-Session-Id`, a stale one is
  ignored rather than refused, and `DELETE /mcp` answers 405 because there is no session
  to terminate — see UPGRADE.md. The 2026-07-28 revision is unaffected: SEP-2567 removed
  sessions and it was already served this way. Session suspension, authorization and
  resumability are unchanged. (#877) ([#929](https://github.com/mcp-hangar/mcp-hangar/pull/929))

### Fixed

- A replica no longer loses tools when a peer restarts a server. The tool-catalogue
  handler was classified as a projection, so it ran on peers' `McpServerStarted`
  events -- but it rebuilds from the local aggregate rather than from the event
  (which carries `tools_count`, not schemas), and the rebuild is a replace. A
  follower whose own copy of that server was cold therefore rebuilt it from nothing
  and deleted a catalogue it was correctly serving. It is now `HandlerKind.LOCAL_VIEW`,
  a third kind for handlers that read local state and so, like effects, must run only
  on the instance that produced the event. (#922) ([#926](https://github.com/mcp-hangar/mcp-hangar/pull/926))
- A restart no longer removes a human-consent gate the configuration still
  declares. The tool-access-policy store held only `allow_list` and `deny_list`,
  and the startup replay rebuilt a policy from exactly those two and assigned it
  over whatever the resolver held. YAML registers policies earlier in the same
  boot, so a target with `tools.approval_list` and any prior REST policy update
  came back **ungated** — and the startup reachability check, running after the
  replay, saw nothing left to demand the gate and started clean.

  The store now persists `approval_list`, `approval_timeout_seconds` and
  `approval_channel`, and the replay hands back whole policies rather than two
  lists a caller has to remember to widen. An existing database is migrated in
  place on first open. A row written by an older build carries NULL approval
  columns; rather than let that erase a gate the resolver already holds, the
  replay carries the in-force gate forward and logs
  `tap_replay_carried_approval_gate`.

  The REST update path now persists the same policy it enforces. It already
  preserved the gate in memory but handed the store the command's two lists, so
  the store held less than the resolver — which is what the next restart replayed.

  No action needed on upgrade. If a gate was lost to this on an earlier restart,
  it comes back on the next one, because the YAML declaration was never the thing
  that went missing. ([#917](https://github.com/mcp-hangar/mcp-hangar/pull/917))
- A `front_door` gateway now starts every configured mcp_server when it starts, so
  `tools/list` stops being a readout of one replica's warm-up history. Previously a
  replica that had started nothing had discovered nothing, so after any restart it
  served an empty tool list to a valid tenant with no client-reachable way to fix it
  (the meta-API is not projected for an ordinary tenant, a known tool name resolves
  against the same empty map, and health checks skip cold servers), and two replicas
  that had warmed different servers answered the same tenant differently. Warming runs
  on its own thread so readiness never waits on a backend handshake, and a backend that
  fails to start is logged (`front_door_warmup_failed`) rather than costing the others
  their projection. `egress` mode is unchanged: backends still start lazily on first
  use. (#878, #885, #886) ([#927](https://github.com/mcp-hangar/mcp-hangar/pull/927))

## [2.6.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.3...v2.6.0) (2026-08-11)

### Added

- **core:** a `front_door` gateway now serves each caller the management tools it
  is authorized to call, instead of serving none to anybody. The mode swapped the
  whole surface at bootstrap -- flat upstream names for everyone, `hangar_*` for
  nobody -- so an operator on a front door had no control plane over MCP at all,
  and turning the mode off to get one handed every agent the entire meta-API.
  Satisfying both meant running two instances.

  The decision is the one the invoke path already makes: a management tool appears
  in `tools/list` exactly when the caller may call it, resolved from the same
  `TOOL_PERMISSIONS` table and the same authorizer. So the list cannot drift from
  the enforcement, a tool that was shown is callable, and a tool that was not is
  still `-32601` if a client guesses the name. The surface is as narrow as the
  caller's role: a principal that may invoke tools and administer nothing sees only
  upstream tools, an operator holding `mcp_servers:read` reads the fleet, and
  neither gets anything it could not already do over REST.

  Stricter than the invoke path in one respect, deliberately: with auth off the
  management surface is empty rather than complete. `--unsafe-no-auth` allows every
  invoke for backward compatibility, but projecting on that rule would hand an
  unauthenticated front-door caller a control plane it does not have today.

  `egress` is unchanged and still serves every caller the whole meta-API -- there
  it *is* the surface, and a client with no `hangar_call` can reach no upstream
  tool at all.

  Also adds `mcp_hangar_projected_tools`, a histogram of how many tools a
  front-door `tools/list` returned, split by `kind=governed|management`. The
  surface sits in an agent's prompt prefix and is paid for on every turn, and
  nothing on the server side could see how large it was ([#912](https://github.com/mcp-hangar/mcp-hangar/pull/912))

### Fixed

- **core:** the README described digest pinning as failing closed without saying
  what it needs to fire. A pin was addressable only per tenant, so on a gateway
  with authentication off it matched nothing -- and the same list two lines down
  states the front-door precondition plainly ("fail-closed on unknown identity"),
  so the omission read as an absence of one rather than an oversight. The bullet
  now names both forms: the all-tenants block that holds any caller, and the
  per-tenant one that needs authentication for a caller to arrive carrying a
  tenant ([#911](https://github.com/mcp-hangar/mcp-hangar/pull/911))
- **core:** the SSRF denylist accepted IPv4-mapped IPv6 forms of addresses it
  refused in ordinary form. `::ffff:169.254.169.254` and `::ffff:127.0.0.1`
  passed both the floor and the human private-range checks because membership of
  an `IPv6Address` in an IPv4 network is always false. `_in_any` now normalizes
  mapped addresses before the check, so mapped and unmapped forms of the same
  host get the same answer at registration and at connect-time pinning ([#900](https://github.com/mcp-hangar/mcp-hangar/pull/900))

### Security

- **core:** twenty-one of the twenty-two `hangar_*` tools authorized nothing.
  `hangar_call` checked `tool:invoke` for every call it dispatched; `hangar_start`,
  `hangar_stop`, `hangar_load`, `hangar_unload`, `hangar_reload_config`,
  `hangar_quarantine`, `hangar_approve` and the rest mutated the fleet on the
  say-so of anyone who got past authentication. The same operations over REST have
  been permission-gated since 2.2.0, so with auth on, one identity in one process
  was refused `POST /api/mcp_servers/{id}/stop` and accepted on `hangar_stop`.

  Four places could have enforced it and none did: the MCP endpoint's ASGI wrapper
  authenticates and never authorizes, no server middleware is installed, the
  shared tool decorator did rate limiting and validation only, and the tool bodies
  dispatch straight to the command bus.

  Authorization is now resolved from the tool name against a declarative table, the
  same inversion the REST route table made and for the same reason -- a tool absent
  from the table is refused rather than public. Each entry mirrors what the REST
  route performing the same operation already requires, so no role changes: reads
  take `mcp_servers:read`, lifecycle takes `mcp_servers:lifecycle`, load and unload
  take `mcp_servers:write`, reload takes `config:reload`, the discovery tools split
  into `discovery:read` / `trigger` / `approve`. Auth off remains allow-all, as it
  already was on the `hangar_call` path, so a `--unsafe-no-auth` gateway is
  unchanged.

  **A principal that could drive these tools over MCP without holding the matching
  permission will now be refused.** If an API key was working through `hangar_*`
  because MCP asked for nothing, it needs the role its REST equivalent has always
  needed ([#910](https://github.com/mcp-hangar/mcp-hangar/pull/910))
- **core:** a `remote` upstream declared in `config.yaml` gets neither half of the
  SSRF policy, and now says so at startup. `enforce_ssrf` is set by the command
  handler behind the REST API and discovery and nowhere else, so an endpoint the
  API answers `400 ssrf_blocked` for -- `http://169.254.169.254/…`,
  `http://10.0.0.5:8080/mcp` -- is accepted from the file without comment, and the
  connect-time re-resolution and IP pinning added in 2.5.0 never runs for it
  either. That second half is the one that closes DNS rebinding, so a config-file
  upstream declared by hostname is re-resolved by httpx on every connect with no
  policy applied.

  The exclusion is deliberate: the operator's file is trusted, a config-file
  upstream on a private address is usually meant, and applying the strict policy
  there would refuse endpoints an operator chose. What was missing is that the
  decision was invisible outside the source -- an operator who moved an upstream
  out of the API and into the file lost two controls silently. Boot now logs one
  line per such upstream naming it, its endpoint, and which protections do not
  apply; an endpoint the strict policy would have refused outright is called out
  in those terms rather than in general ones. Nothing is refused and no upstream
  changes behaviour ([#908](https://github.com/mcp-hangar/mcp-hangar/pull/908))
- **core:** digest pinning enforced nothing on a gateway with authentication
  disabled, which is the configuration most evaluations run. A pin was
  addressable only under `tool_projection.tenant_overrides.<tenant>.pins`, and
  `resolve_pin` looked it up by tenant id -- but a tenant id reaches the call path
  from exactly one place, `Principal.tenant_id`, and with auth off every caller is
  anonymous and carries `None`. So no pin was ever matched, the gate took its "no
  pin" branch, and every call went through unverified while `initialize` kept
  advertising `io.mcp-hangar.digest-pinning` with all three enforcement modes.
  Drift stayed computable and nothing stopped it. The same miss took out the task
  path with it: the pin is what `create_task` binds a relayed task to, so tasks
  were never bound to a digest either and the fail-closed re-verification on
  result retrieval never had anything to check.

  Pins can now be declared for all tenants, alongside the `withdrawn:` list they
  mirror, and that block holds a caller carrying no tenant identity:

  ```yaml
  tool_projection:
    digest_enforcement: block
    pins:
      refund: <sha256>
  ```

  A pin declared for a specific tenant still wins over the all-tenants one for
  that tenant -- narrowest first, the order the tool-access policies already
  resolve in. And a configuration that declares per-tenant pins while
  authentication is off no longer starts: it names the pins it found and the auth
  setting that makes them unmatchable, and points at both ways out ([#907](https://github.com/mcp-hangar/mcp-hangar/pull/907))

## [2.5.3](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.2...v2.5.3) (2026-08-11)

### Added

- **core:** a `front_door` gateway that serves no tools now says why. Three very
  different situations used to produce the same 200, the same `{"tools": []}` and
  nothing in the log: the caller carried no tenant identity (a fail-closed deny),
  the replica had discovered nothing yet (a wrong answer that a restart produces
  on its own), or policy removed every tool (the one case where the empty list is
  true).

  An operator watching a front door that had just been rolled saw healthy pods, a
  successful response, and tenants reporting that everything had vanished.

  Two new signals:

  - a log line naming the cause -- WARNING for the two faults, INFO for the
    correct answer, throttled to once a minute per cause so a standing condition
    cannot bury its own first occurrence;
  - `mcp_hangar_empty_projection_total{reason=...}`, with reasons `no_identity`,
    `nothing_discovered` and `filtered`. Not labelled by tenant, deliberately: a
    public front door has unbounded tenant cardinality, and the log line carries
    the tenant for the follow-up. The counter is not throttled, so the rate stays
    truthful.

  The missing-identity deny in `front_door` mode also logs at WARNING now,
  naming that branch specifically rather than leaving a policy-shaped symptom
  behind a wiring problem. ([#895](https://github.com/mcp-hangar/mcp-hangar/pull/895))

### Fixed

- **core:** the handshake advertised `prompts` and `resources` and served
  neither. A client that sees `prompts` advertised and gets `[]` back concludes
  the upstream *has no prompts* -- which is a different statement from *this
  gateway does not carry prompts*, and the client had no way to tell them apart.
  Both capabilities are now withdrawn while nothing is registered under them, on
  `initialize` and on the SEP-2575 `server/discover` result alike.

  `prompts/*` and `resources/*` consequently answer `-32601` (method not found)
  instead of an empty list. That is the honest reply from a server that does not
  claim the capability, and a conformant client that reads capabilities first
  will not call them at all. If you have a client that calls `prompts/list` or
  `resources/list` unconditionally and treats an error as fatal, it needs to
  check the advertised capabilities -- which it should have been doing.

  Derived rather than hard-coded: the capability follows what is actually
  registered, so proxying an upstream's prompts and resources (#889) turns both
  back on without touching this code. ([#891](https://github.com/mcp-hangar/mcp-hangar/pull/891))
- **core:** the gateway introduced itself to every upstream MCP server as
  `mcp-registry / 1.0.0` -- a product name that has not existed for a long time,
  at a literal version that never moved while the gateway sending it was 2.5.2.
  It now sends `mcp-hangar` and the running package version, read from package
  metadata so the two cannot drift apart again.

  This is what an upstream operator sees in their logs when working out who is
  calling them, and it is not only the handshake: the same identity rides
  `params._meta["io.modelcontextprotocol/clientInfo"]` on every request to a
  modern upstream. Nothing needs to change on your side -- but if you match on
  `mcp-registry` in upstream log filters, alerting or client-specific
  workarounds, those match on `mcp-hangar` from this release. ([#890](https://github.com/mcp-hangar/mcp-hangar/pull/890))
- **core:** the gateway never finished the MCP handshake with an upstream. It
  sent `initialize` and then went straight to `tools/list`, skipping the
  `notifications/initialized` the lifecycle requires, so every upstream -- stdio,
  docker and remote alike -- was left permanently mid-handshake.

  A server is entitled to defer work until that notification arrives, and the
  official reference server does exactly that: a tool registered in its
  `oninitialized` handler was neither listed nor callable through Hangar (12
  tools discovered where a finished session sees 13), with nothing logged to
  suggest anything was missing. If your upstream registers tools, prompts or
  resources on initialization, this release discovers them for the first time --
  so a catalogue may legitimately grow after upgrading.

  The notification is best-effort: an upstream that mishandles it gets a warning
  in the log, not a failed start. Both transports gained a `notify()` primitive
  to make it possible at all -- neither could previously send a message without
  an id. ([#892](https://github.com/mcp-hangar/mcp-hangar/pull/892))
- **core:** a tool definition lost everything except `name`, `description` and
  `inputSchema` on its way through the gateway. `title`, `annotations`,
  `execution`, `icons` and the upstream's `_meta` were discarded at discovery, so
  no surface downstream could serve them.

  `annotations.readOnlyHint` and `destructiveHint` are how a client or an agent
  harness decides whether a call needs a human in front of it. Behind Hangar
  every tool looked alike, so that decision degraded to pattern-matching on tool
  names -- the failure mode a policy enforcement plane exists to remove. `title`
  is what a UI shows, and `execution.taskSupport` is how a client knows a tool
  must be invoked as a task.

  All five now travel from `tools/list` through to `hangar_tools`, the
  `front_door` flat projection and the REST tool views alike. The flat projection
  additionally regains `outputSchema`, which it dropped even though the other
  surfaces kept it -- a client behind the front door had nothing to validate
  structured output against.

  Tool digests are deliberately unchanged: the pinned surface is still
  `{description, inputSchema, outputSchema}`, so no existing pin is invalidated
  by this release. Whether `annotations` belongs inside the pinned surface is a
  separate question, filed rather than decided here. ([#893](https://github.com/mcp-hangar/mcp-hangar/pull/893))

## [2.5.2](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.1...v2.5.2) (2026-08-10)

### Fixed

- **core:** `MCP_TRUSTED_HOSTS` did not reach the MCP endpoint. The app was built
  with the SDK's default transport security, which derives its allowlist from the
  SDK's own bind host, so `/mcp` answered `421 Invalid Host header` to the
  gateway's Service DNS name and to every Ingress host while the REST API on the
  same process accepted them -- the two read different lists. Both serving paths
  now build the guard from the configured allowlist, expanding each entry to match
  with and without a port (the SDK compares the raw `Host` header, everything else
  in Hangar strips it), with `*` opting out as it does elsewhere. Origins come from
  the same `MCP_CORS_ORIGINS` list the WebSocket handshake already used. Permitted origins are the served hosts plus `MCP_CORS_ORIGINS`, so a
  same-origin browser request keeps working while a foreign one is still
  refused ([#871](https://github.com/mcp-hangar/mcp-hangar/pull/871))
- **core:** `provider-admin` could not deliver an egress policy. The route table
  maps `/api/mcp_servers/{id}/l7_policy` to `policy:write` -- the permission the
  role holds and the reason it exists -- but the two handlers ran a second,
  in-handler check for `mcp_servers:write` on top, which `provider-admin` does not
  hold and `developer` does. The operator's push answered 403 while the
  `MCPEgressPolicy` CR still reported `Compiled` and `BackstopApplied`, so the
  policy enforced its L3/L4 half and silently dropped its L7 half. Authorization
  for both handlers now comes from the route table alone ([#870](https://github.com/mcp-hangar/mcp-hangar/pull/870))
- **core:** `tool_access.mode: front_door` projected zero tools to every
  authenticated tenant over Streamable HTTP. The SDK hands each lowlevel handler a
  per-request context carrying the HTTP request, and therefore the authenticated
  principal; the `tools/list` and `tools/call` adapters were handed it and dropped
  it, so both read `identity_context_var` -- which the ASGI wrapper sets in a
  different task -- found nothing, and the resolver took its `member_id is None`
  deny-all branch. An empty tool list is indistinguishable from "no tools
  configured", so nothing said so. Both adapters now bind the caller for the
  duration of the call, through the same bridge tool bodies already used ([#874](https://github.com/mcp-hangar/mcp-hangar/pull/874))
- **core:** `mcp-hangar auth bootstrap-admin` refused to run on a deployment that
  had made the one storage decision. It consulted only `auth.storage.driver`,
  which defaults to `memory`, and answered "driver 'memory' is not durable" -- on
  exactly the deployments where it is the only way in, since `/api/auth/**`
  requires an admin principal with no carve-out for the first call. It now uses
  the backend `persistence.backend` selected, which is durable by construction.
  The claim it makes also stopped colliding with a configured `auth.role_assignments`
  entry for the same principal: the admin assignment is inserted with the same
  conflict tolerance `assign_role` has always had, on both backends ([#873](https://github.com/mcp-hangar/mcp-hangar/pull/873))
- **core:** an auth-enabled gateway could not start on a fresh database once
  `persistence.backend` was set. The one-storage branch in `auth/bootstrap.py`
  returns the backend's API-key, role and tool-access-policy stores as they are,
  and those three keep schema creation in `initialize()` -- which nothing called,
  on either backend. Startup reached the auth bootstrap and died on
  `relation "roles" does not exist`, or, with no `role_assignments` configured to
  trip it, on `tool_access_policies` a few lines later; SQLite failed the same way
  with `no such table: roles`. Both backends now initialise those stores when they
  build them, the way the event store already did. The legacy
  `auth.storage.driver: postgresql` branch also stopped returning no tool-access
  store at all, which is why naming the backend a second time was not a workaround
  either ([#869](https://github.com/mcp-hangar/mcp-hangar/pull/869))

## [2.5.1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.0-rc.4...v2.5.1) (2026-08-10)

### Fixed

- **core:** the `hangar_sources` tool description now lists the `id` field the tool
  has returned since 2.5.0. That docstring is what an MCP client reads verbatim to
  learn the tool's shape, and it still described the seven pre-2.5.0 fields, so a
  client had no way to know the addressable id was there. The description also says
  what the id is for: it is the id `/api/discovery/sources/{id}` takes, and a source
  declared in `config.yaml` derives it from its `source_type`, so it survives a
  restart. No behaviour change -- the returned payload is the same as in 2.5.0 ([#843](https://github.com/mcp-hangar/mcp-hangar/pull/843))
- **core:** a `coordination:` block with no `persistence.backend` is refused at
  startup instead of booting into the failure the block exists to prevent. The
  check only refused a backend that could not be shared and returned early when no
  backend had been selected at all -- but with no backend there is no lease keeper
  either, so `may_manage()` is True in every process, every replica runs the
  management loops and every replica reports `manages_fleet: true`. A deployment
  still on the legacy per-subsystem keys (`event_store.driver: postgresql`,
  `auth.storage.driver: postgresql`) shares one database and declares
  coordination, and was never asked the question. Such a configuration now fails
  the boot with a message naming the missing decision: set
  `persistence.backend: postgresql`, or remove the `coordination:` block to run as
  a single gateway. ([#841](https://github.com/mcp-hangar/mcp-hangar/pull/841))

### Security

- **core:** the connect-time SSRF re-check shipped in 2.5.0 did not survive a
  restart. The flag that turns it on -- together with the provenance and the
  runtime-reported addresses it judges an endpoint against -- has a place on the
  stored configuration snapshot, but nothing ever wrote it there, so every server
  rebuilt from its record came back unguarded: `enforce_ssrf` off, provenance
  HUMAN, no runtime addresses. That covers both paths that rebuild a server from
  the record -- recovery on restart, and the fleet projection on a replica that
  learned of the registration from the event log. Registration-time validation
  was unaffected, which is what kept this quiet: the endpoint was still checked
  once, when it was registered, and only the DNS-rebinding defence on every later
  connection lapsed.

  Read it this way for a running deployment: on 2.5.0 the connect-time guard
  protected only remote servers registered by the process currently serving them.
  A gateway that has restarted since a remote server was registered has been
  connecting to that endpoint with registration-time validation alone, and so has
  any replica that did not perform the registration itself. A discovered server
  keeps its runtime-scoped addresses across the same trip, so its legitimate
  private container address is not refused once the guard is back on.

  **Servers registered under 2.5.0 are covered by the upgrade.** Recording the
  flag on its own would have fixed new registrations only: every row already in
  the store says `enforce_ssrf: false`, because that was the field's default
  before anything wrote it, and an update does not repair such a row -- it records
  the aggregate that was itself rebuilt with the flag off. Those servers would
  have stayed unguarded permanently, curable only by deleting and re-registering
  them. So the guard is now derived when the record shows what registration
  already checked: a remote mode with an endpoint. Restart the gateway and the
  existing fleet is guarded; no re-registration, no edit to the store.

  Guarding is also pinning, which is the part worth knowing before it surprises
  someone. A guarded connection goes to one address the policy validated, with
  the original hostname kept for the `Host` header and the certificate, rather
  than to whichever address the client would have picked. For an upstream behind
  several A or AAAA records that means the resolver's first answer instead of
  httpx's own multi-address fallback, so a dead address behind a healthy name now
  fails the call rather than being skipped. That behaviour shipped in 2.5.0; what
  changes here is how much of the fleet it applies to.

  One case is deliberately left as the upgrade found it. A row written before
  this fix says nothing about provenance, so a server that discovery registered
  comes back as HUMAN with no runtime addresses -- and applying the strict policy
  to a container or pod address would refuse, on every call, an upstream that
  works today. The endpoint is what tells the two apart: an endpoint that passed
  the strict check at registration cannot be a private literal, so a stored
  endpoint that *is* one can only have come from the scoped discovery path, and
  it keeps 2.5.0's behaviour rather than becoming an outage. Re-registering such
  a server -- or letting discovery register it again -- writes the real
  provenance, and the guard comes back with the scoping that makes its address
  legitimate. ([#842](https://github.com/mcp-hangar/mcp-hangar/pull/842))

## [2.5.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.0-rc.4...v2.5.0) (2026-08-09)

### Fixed

- **core:** environment-variable interpolation works again on the programmatic
  `bootstrap(config_dict=...)` / facade path. A rc.4 change removed the per-auth
  interpolation and left the whole-document pass only on the file loader, so a
  config passed as a dict had no interpolation at all: `auth: {bearer_token:
  "${API_TOKEN}"}` was sent literally to the upstream (a 401 on every call) and a
  missing variable no longer failed the boot closed. The dict path now
  interpolates the document once at its entry point, matching the file path and
  restoring fail-closed-on-missing-variable. ([#838](https://github.com/mcp-hangar/mcp-hangar/pull/838))
- **core:** the persistence-backend completeness guard now invokes each concern
  instead of only checking that the method exists. A third-party backend whose
  concern method is callable but returns `None` -- exactly how the tool-access
  policy store was once silently disabled -- passed the guard, because it only
  tested callability. `create_backend` now calls each concern and treats a `None`
  return as a missing concern, so an incomplete backend is refused as the
  docstrings promise. Built-in backends cache their adapters, so the extra call is
  free. ([#837](https://github.com/mcp-hangar/mcp-hangar/pull/837))
- **core:** the "certificate verification is off for this upstream" startup
  warning no longer fires when `tls.verify_ssl: false` is set alongside a
  `tls.ca_cert_path`. The CA path wins in the client -- verification is enforced
  against that CA -- so the old warning contradicted the actual behaviour and sent
  an operator debugging a failed handshake toward the very setting doing the
  enforcing. That combination now logs an accurate message saying verification is
  enforced against the configured CA; the "verification is off" warning fires only
  when verification is genuinely off. ([#836](https://github.com/mcp-hangar/mcp-hangar/pull/836))
- **core:** fixes on the auth and config-error surfaces. `auth bootstrap-admin`
  no longer prints an API key that no authenticator would accept (an OIDC-trusted
  deployment with `auth.api_key.enabled: false`), and a flagless re-run no longer
  claims the one-shot claim is unspent when it has already been spent -- both
  answers now consult the store first, via a new read-only
  `is_initial_admin_bootstrapped` check that costs nothing. `POST /api/config/reload`
  maps only a genuine "cannot write the backup file" condition to `503`; an
  operator-input config error is now a `500` with a sanitised message instead of a
  retryable `503` that surfaced internal exception text (paths, server ids) to the
  caller. The auth store's read-only PostgreSQL paths now commit or roll back
  rather than leaving a borrowed connection idle in transaction. ([#835](https://github.com/mcp-hangar/mcp-hangar/pull/835))
- **core:** discovery source management now works end to end. Triggering a scan
  awaits the discovery cycle instead of dropping the coroutine, so the endpoint no
  longer reports a fabricated success while nothing runs; enabling, disabling, or
  reconfiguring a source reaches the running source rather than only its registry
  spec, so the listing and the toggle agree; a deleted source is no longer
  re-advertised with an id whose scan/enable routes then answer `404`; and the id
  is emitted from the source status itself, so the REST API and the MCP
  `hangar_sources` tool both carry it. The mutating source-management surface is
  labelled Preview for 2.5.0, signalled by an `X-Hangar-Preview` response header. ([#834](https://github.com/mcp-hangar/mcp-hangar/pull/834))

### Security

- **core:** the SSRF check that guards a remote MCP server's endpoint is now
  enforced at connect time, not only when the server is registered. httpx
  re-resolved the hostname itself on every connection with no second check, so a
  human-registered name that resolved to a public address at registration could be
  re-pointed at an internal one -- `169.254.169.254`, `10.x`, `127.0.0.1` -- before
  the next tool call (DNS rebinding). The client now re-applies the same policy on
  every request and pins the connection to the validated IP, keeping the original
  hostname for the `Host` header and TLS certificate verification. A
  discovery-sourced endpoint may still be private, but only at an address the
  container runtime reported for it. ([#836](https://github.com/mcp-hangar/mcp-hangar/pull/836))

## [2.5.0-rc.4](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.0-rc.3...v2.5.0-rc.4) (2026-08-08)

### Fixed

- **core:** a secret containing a literal `${...}` no longer fails the boot. Moving `${VAR}` interpolation out to the whole document left the original call inside `mcp_servers.<id>.auth` where it was, so that one block was interpolated twice. The second pass reads the output of the first, which makes it not a no-op: a generated password like `R9${x}q!` arrived correctly from the environment and was then read as another reference, refusing startup with `Required environment variable '${x}' is not set` -- or, if `x` happened to be set, substituting again and configuring the upstream with a credential nobody wrote. The document is interpolated once as it is read, and what comes out is a value rather than more configuration. `UPGRADE.md` now also names the consequence that moved with it: an unset `${VAR}` with no `:-default` has always been fail-closed and now fails the whole boot, not only the `auth` sub-block ([#830](https://github.com/mcp-hangar/mcp-hangar/pull/830))
- **core:** the discovery registry lists what the orchestrator runs, rather than a second reading of `config.yaml`. Giving a configured source an id fixed "visible but not scannable" by parsing the configuration again in `_register_configured_sources`, and the two readings disagreed in both directions. A source whose `mode` was misspelt was built anyway -- the builder resolves anything that is not `authoritative` to additive -- so it appeared in `GET /api/discovery/sources` with an id, while `DiscoveryMode("additivee")` raised in the second reading and `POST /api/discovery/sources/<id>/scan` answered 404 for the id the listing had just handed out. A source that failed to build, on a missing optional dependency or any other error, was absent from the listing and registered regardless, so a scan on its derived id answered `200 {"scan_triggered": true}` for something that does not exist. Membership now comes from `DiscoveryOrchestrator.get_sources()`, and a spec carries the mode its built source is actually running in; the configuration is still where a source's own settings come from ([#831](https://github.com/mcp-hangar/mcp-hangar/pull/831))
- **core:** `auth bootstrap-admin` no longer advises a re-run at the moment re-running becomes impossible. Without `--show-key` it closed with "If API keys are this deployment's only authenticator, re-run with `--show-key` -- the claim is one-shot, so do it now rather than after", and the claim had been spent by the line above it: the second run answers "The initial administrator has already been bootstrapped", the key it minted is stored hashed, and `bootstrap-admin` is the only subcommand in the auth CLI. An API-key-only deployment was left with a credential nobody had ever seen and no way to reach its own gateway. The choice is made before the claim instead: with no trusted OIDC issuer, omitting `--show-key` is refused while the claim is still unspent, so the advice can actually be taken. A configuration with no authenticator at all is refused on the same grounds. The refusal for an already-spent claim now names recovery that does not require already holding the credential, and the closing message on an OIDC deployment states that the secret is gone rather than implying a second chance ([#833](https://github.com/mcp-hangar/mcp-hangar/pull/833))
- `mcp-hangar auth bootstrap-admin --show-key` prints the bootstrap API key's secret, so a deployment whose only authenticator is API keys can obtain its first credential. Every `/api/auth/**` route requires an admin principal with no carve-out for the first call, so the first key cannot be minted over HTTP; the command that exists to break that created an API key as part of its atomic claim and then discarded the secret. That default is right for the case it was built for -- an OIDC principal authenticates on its own identity and needs no key -- but it left an API-key-only deployment with no way to reach its own gateway, and with an unusable key row in its database. The secret stays unprinted unless the flag asks for it, and the message when the flag is absent now names it, because the claim is one-shot and an operator who learns about it afterwards has no second chance. ([#824](https://github.com/mcp-hangar/mcp-hangar/pull/824))
- `POST /api/config/backup` says why it failed instead of answering with a bare 500. The backup is written beside the configuration file, so it fails wherever that directory is not writable by the process -- which is every deployment using the published image, where `/app` belongs to root and the gateway runs as `hangar`. The caller received `An internal server error occurred.` with the real reason (`PermissionError: [Errno 13] Permission denied: 'config.yaml.bak1'`) visible only in the log, which tells an operator the gateway is broken when the gateway is working and the filesystem said no. An `OSError` from the write now becomes a `ConfigurationError` naming the path and the reason, and `ConfigurationError` reaching the API maps to 503 rather than 500: a statement about the deployment, not about the request. Anything that is not an `OSError` is still an unexpected failure and still surfaces as one. ([#823](https://github.com/mcp-hangar/mcp-hangar/pull/823))
- A discovery source declared in `config.yaml` can now be named and scanned through the API. There were two registries and a configured source only ever reached one of them: it went to the orchestrator, which runs it, while the UUID-keyed `DiscoveryRegistry` was created empty and only the REST API ever wrote to it. So `POST /api/discovery/sources/<id>/scan` answered 404 for every id an operator could obtain, and `GET /api/discovery/sources` returned no `id` to try in the first place. Configured sources are registered at bootstrap with an id derived from the source type rather than generated -- the orchestrator keys its sources by type, and a random id would change on every restart, so a scan a script triggered yesterday would address nothing today -- and the listing now carries that id, because it is what every other route on the resource takes. Registering changes nothing about execution: the orchestrator still owns the running source. ([#825](https://github.com/mcp-hangar/mcp-hangar/pull/825))

### Security

- Per-server TLS settings now reach the connection. `httpx.Client(verify=...)` only configures the transport httpx would have built for itself, and this client passes `transport=` explicitly for retries -- so the transport was constructed without `verify` and used the default trust store, silently discarding every TLS setting an operator could write. Measured against a self-signed upstream on 2.5.0-rc.3, through the same httpx: `verify=False` with no explicit transport connects, `verify=False` plus a transport built without it fails, and a transport built with `verify=False` connects. It failed **closed**, which is why it went unnoticed -- `tls.verify_ssl: false` simply did not work and looked like a stubborn certificate. `tls.ca_cert_path` rides the same argument and was discarded the same way, and that one has no safe reading: it is how a deployment trusts its own internal CA, so an upstream behind a private CA was unreachable with no way to fix it from configuration. Both are honoured now, and `verify=` is gone from the client call so there is no longer a second place that looks like the setting and is not. ([#822](https://github.com/mcp-hangar/mcp-hangar/pull/822))
- Disabling certificate verification for an upstream now logs a warning naming the endpoint. The setting changed meaning in this release: until 2.5.0 `tls.verify_ssl: false` was accepted and silently discarded, so a configuration carrying it did nothing, and now it does exactly what it says. Whoever wrote that line may no longer be reading, and the value was previously visible only as a field on an ordinary info line among dozens. Trusting a private CA through `tls.ca_cert_path` is verification and stays quiet. ([#826](https://github.com/mcp-hangar/mcp-hangar/pull/826))

## [2.5.0-rc.3](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.0-rc.2...v2.5.0-rc.3) (2026-08-07)

### Changed

- A `coordination:` block plus a `subprocess`, `docker` or `container` server in `config.yaml` now refuses to start, naming every offender at once. Registering such a server through the API was already refused where storage is shareable, and launching one on a follower refused again -- but a server declared in configuration goes through neither path: it is loaded on every replica and only the lease holder can start it. What an operator saw was not an error but `GET /api/mcp_servers/<id>/tools` answering with five tools on one pod and an empty list on the others, plus a 409 from whichever replica the load balancer picked. The question is asked on the axis the operator controls: the `coordination:` block is the statement that these replicas are meant to be one gateway, so without it a single gateway that merely uses PostgreSQL keeps running its child processes exactly as before. The registration refusal also stops telling a single instance to run a single instance -- the condition is storage peers can share, and the message now says so and names `persistence.backend: sqlite` as the alternative to `remote` mode. ([#815](https://github.com/mcp-hangar/mcp-hangar/pull/815))

### Fixed

- `${VAR}` in configuration is interpolated everywhere, not only inside `mcp_servers.<id>.auth`. The documentation has always described it as a property of configuration -- the production checklist tells an operator to keep secrets out of the file this way, the transport guide says "configuration values support environment variable interpolation", and the reference documents it for Langfuse keys -- and it was implemented for one sub-block. Found by running the multi-replica cookbook against the published 2.5.0-rc.2 image: `persistence.postgresql.password: ${HANGAR_DB_PASSWORD}` reached psycopg2 as those twenty-two literal characters and every pod failed with `password authentication failed`, with the variable correctly set from a Secret exactly as the recipe and the Helm chart both instruct. The alternative -- writing the password into the file -- is what the checklist exists to prevent. A variable that is unset and has no default still fails the boot naming itself, which it did before for the one block that worked. ([#817](https://github.com/mcp-hangar/mcp-hangar/pull/817))

## [2.5.0-rc.2](https://github.com/mcp-hangar/mcp-hangar/compare/v2.5.0-rc.1...v2.5.0-rc.2) (2026-08-07)

### Fixed

- Three things a lopsided replica set would not tell you. **Discovery configured on a replica that does not hold the management lease now says so.** The gate is right -- a follower running discovery would deregister servers off a view it does not own -- but it closed in silence, while every replica's boot log already announced its source count, which reads as "watching". A replica set whose discovery-configured replicas are not the holder discovers nothing; measured at zero cycles until the holder was killed. Paced like the tailer and the keeper: the first skipped cycle, then rarely, and one line when it resumes. **`GET /api/system` now reports the management lease actually in force** -- holder, generation, remaining time -- next to the tenure this instance would write. `expires_at` is written by the holder from *its own* `lease_ttl_s`, so a replica configured for a 10-second tenure that finds a holder configured for 60 waits 60: measured at 52 seconds, with nothing anywhere reporting the number being waited for. A materially longer tenure than configured is logged once as the config drift it is. **A follower refusing to start a `subprocess` or `docker` server now answers `409`, not `500`.** The refusal is correct and travelled as a generic start failure, telling the caller a gateway was broken when it was behaving exactly as designed; it is now a domain error the model lets through unwrapped, and its message names `manages_fleet: true` as the instance to ask. ([#813](https://github.com/mcp-hangar/mcp-hangar/pull/813))
- Two replicas booting against the same empty PostgreSQL no longer race to create the schema. `CREATE TABLE IF NOT EXISTS` reads as the safe spelling and is not concurrency-safe in PostgreSQL: two sessions can both find a table absent, both issue the create, and the loser dies on a system-catalog unique violation (`duplicate key value violates unique constraint "pg_type_typname_nsp_index"`). Sequentially it is idempotent, which is why it survived every test and every single-gateway deployment -- and a replica set is exactly a set of processes that start at the same instant against the same database. Measured on the first HA candidate: `replicas: 3` against an empty database crashed **two of the three** pods on first boot, three trials out of three, and the deployment then converged on the restart, so the only trace was a restart counter and a PostgreSQL catalog error in a log nobody reads. Every DDL statement in the PostgreSQL backend now takes one transaction-scoped advisory lock, and the saga store's dialect-agnostic migration runner takes the same key on a session lock. A structural test refuses the next adapter that creates a table without it. ([#812](https://github.com/mcp-hangar/mcp-hangar/pull/812))

## [2.5.0-rc.1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.4.0...v2.5.0-rc.1) (2026-08-07)

### Added

- **core:** the event log can now be followed without losing events. `global_position` is a `BIGSERIAL`, and sequence *allocation* order is not commit order: two appenders can be handed positions 5 and 6, the holder of 6 can commit first, and a cursor that has advanced past 6 never sees 5 arrive. The PostgreSQL store's own docstring described this as reordering a reader "should tolerate"; measured against PostgreSQL 16, the event at 5 is not delivered late, it is **never delivered at all** ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 0.2 of the HA work in [#789](https://github.com/mcp-hangar/mcp-hangar/issues/789)). Nothing has broken so far only because the one caller reading by position runs once at startup, after the tail has settled. `IEventStore` gains `read_since(cursor)` and `tail_head()`, taking an opaque `TailCursor` rather than a number -- a position is a resume point only where appends are serialized, and a caller holding an integer has already assumed one store's answer. SQLite and the in-memory store keep resuming from a position and say why next to the declaration; PostgreSQL resumes from a transaction watermark (`pg_snapshot_xmin`) instead, which cannot pass a transaction that is still open. The alternative in the design note -- allocating positions from a counter row inside the append transaction -- was measured and rejected: it puts a row lock on the path of every tool invocation and stops scaling at four concurrent writers (~1650 appends/s flat against ~6600 for the sequence at sixteen, p99 5ms to 49ms), while the `xid8` column it replaces costs nothing measurable. A store that neither declares its positions commit-ordered nor implements its own resume token is refused loudly rather than allowed to skip quietly, and existing PostgreSQL installations take the new column by an instant `ALTER`, not a table rewrite ([#793](https://github.com/mcp-hangar/mcp-hangar/pull/793))
- **core:** a server registered on one replica is now servable on the others ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 2.3). It lived in the memory of whichever replica had registered it -- by an operator's request, or by the discovery loop that replica happened to be running -- so the others answered "no such server" for it until they restarted, and which replica knew about which server depended on where the load balancer had sent each registration. Fleet membership is now a projection: the tailed `McpServerRegistered` / `McpServerDeregistered` events update every replica's fleet. The event is the notification and the shared record is the content -- `McpServerRegistered` carries an id, a source and a mode, and enriching it with a whole configuration would mean versioning a persisted, replayable event every time a configuration field was added. Instead the projection reads the row from the storage backend every replica already shares, which works because of an ordering chosen earlier for a different reason: registration writes the snapshot **before** it joins the fleet and before it publishes, so the row is committed by the time the event exists. A server already present is left exactly as it is, because the local copy may be running and the record describes configuration rather than state; a missing record is reported rather than guessed at, since inventing a configuration would put a server in the fleet that nobody described. One definition of "rebuild a server from its record" is now shared with recovery, which had the only copy -- two would drift the first time a field was added, and the drift would show up as a field quietly absent on replicas that learned by tail ([#804](https://github.com/mcp-hangar/mcp-hangar/pull/804))
- **core:** the fleet-management loops now run only while this instance holds the management lease. Discovery, TTL deregistration and the metric snapshot worker are asked, **once per cycle**, whether they may run ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phases 1.2 and 1.3 of the HA work). Per cycle rather than once at startup: a lease lost mid-life has to stop the next cycle, not the next process, and an instance that checked once and kept going is precisely the stalled leader that fencing exists to catch -- fencing being a last line rather than a plan. The keeper acquires when the lease is free, renews on an interval well under the tenure, and treats losing it as an ordinary outcome rather than an error to retry through. Losing it has two shapes and only one is an answer: a renewal refused by the database is definite, while a database that does not answer at all leaves this instance knowing nothing -- so there is a **renew deadline**, measured with a monotonic clock and deliberately shorter than the TTL, after which the instance declares the lease lost by itself rather than waiting to be told. It gives up slightly early instead of slightly late, which is the direction where the failure is "nobody manages for a few seconds" rather than "two instances manage at once". Defaults mirror Kubernetes leader election (15s tenure, renewed every 5s, given up after 10s without a successful renewal) and are configurable under `coordination`. Stopping releases the lease, which is the difference between a rolling update that pauses management for a moment and one that pauses it for a TTL per pod. **Garbage collection and health checks are deliberately not gated**: they act on this replica's own child processes and its own connections to upstreams, so gating them would leak an idle subprocess on every follower and leave followers unable to notice that an upstream they serve traffic to had died. Without a storage backend there is no lease and nothing changes -- a standalone gateway has no peers to disagree with ([#796](https://github.com/mcp-hangar/mcp-hangar/pull/796))
- **core:** a management lease, so only one instance converges the fleet. Discovery, garbage collection, health decisions and TTL deregistration are convergence loops, and three replicas running them against one shared database produce the failure nobody can debug afterwards: a server registered by one replica and deregistered by another, in the same second, forever ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 1.1 of the HA work). `IManagementLease` is a row with a TTL and a **generation**, held in the storage backend the deployment already chose -- deliberately not a Kubernetes `Lease`, because core has to run on compose, on podman and from a `pip install`, and a coordination primitive that only exists inside a cluster would make those second-class. The generation is what makes a leader safe: a TTL alone does not stop a stalled holder from waking up after its lease expired and finishing a sweep that undoes its successor's work, because it has no way to know time passed. Carried into the `WHERE` clause of a destructive write, a stale generation matches zero rows and does nothing. PostgreSQL takes every decision inside a single conditional statement, against the database's clock rather than the caller's -- replicas do not agree about the time, and a lease compared against a local clock expires early on a fast node and late on a slow one; verified against a real server with sixteen threads racing for one lease. SQLite always grants it, which is the correct answer rather than a shortcut: its file admits one writer, so an unexpired row there can only be a dead predecessor, and waiting out the TTL would leave a standalone gateway managing nothing for fifteen seconds after a restart to protect a peer that cannot exist. It is still a real row, because the generation has to be monotonic across restarts for fencing to mean anything. Coordination is persisted state like any other, so it is the eleventh concern a backend must provide or be refused ([#795](https://github.com/mcp-hangar/mcp-hangar/pull/795))
- **core:** event handlers now declare what they do, and that decides where they may run ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 2.2). A **projection** keeps a local view -- the tool catalogue, a risk score, the websocket event feed -- and must run on every replica for every event, whoever produced it, or it is a view of a third of the system. An **effect** does something outward -- exports to a SIEM, charges a budget, sends an alert, takes an enforcement action -- and runs only on the instance that produced the event, which is exactly-once by construction because a tool call happens on exactly one replica. `subscribe` and `subscribe_to_all` now require `kind` as a keyword argument: there is no default that is right for both, and both wrong answers are silent -- an unclassified effect exports the same tool call from three replicas, an unclassified projection leaves two of them with a stale view. **This lands before the tailer that makes it matter, not after**: the tailer is what creates the duplicate-effect problem by delivering peers' events to every replica, so classifying afterwards would mean shipping a period in which three replicas send three CEF records for one tool call, and then re-deriving on the side what each of a dozen handlers actually does. Every handler in the tree is classified, with the reason written next to it, and a test fails the build on a subscription that declares no kind. Two are worth naming: the compliance exporter is an effect, because a wrong answer there is a compliance problem rather than a performance one; the tool-projection registry is a projection, because a replica that only learned about servers it started would serve a fraction of the catalogue, and which fraction would depend on where the load balancer sent each start request ([#799](https://github.com/mcp-hangar/mcp-hangar/pull/799))
- **core:** every domain event now carries the instance that produced it. `DomainEvent` had exactly two fields, `event_id` and `occurred_at`, so several replicas writing to one shared log produced rows nobody could tell apart. That is an audit inconvenience at one replica and a correctness gap at three: a replica publishes an event to its own handlers *and* appends it to the log it will later tail, so without a producer on the row it cannot distinguish its own append from a peer's -- it either delivers everything twice or skips a peer's event, with no way to know which ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), the first item of the HA work in [#789](https://github.com/mcp-hangar/mcp-hangar/issues/789)). The identity is **minted, not configured**: `HANGAR_INSTANCE_LABEL` (or the hostname, which under Kubernetes is the pod name) only prefixes it, and a per-process suffix is always appended, so three replicas rolled from one ConfigMap cannot end up sharing an identity -- a failure that nothing would have caught, since each would then treat its peers' events as its own and go quiet with every health check green. Events are persisted, so this is schema evolution on a compatibility surface: a row stored before the field existed still replays, and reads as `UNKNOWN_PRODUCER` rather than as "produced by whoever is reading it" -- the reverse would have a tailer silently drop history as its own work ([#792](https://github.com/mcp-hangar/mcp-hangar/pull/792))
- **core:** a replica now follows the shared event log, so it knows what its peers did ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 2.1). Without it, a gateway with three replicas has three separate views of one fleet: a server started on one is missing from another's tool catalogue, a risk signal seen by a third never reaches the other two, and which servers a replica knows about depends on where the load balancer sent each request. Three properties make following the log safe, and each was built before this so that none of them is a promise made here. It **skips its own events** -- a replica publishes locally *and* appends to the log it tails, so without the producer on the row it would apply everything it did a second time, and idempotent handlers would hide that until one of them was not. It **delivers to projections only**, because an effect belongs to the instance that produced the event, and running effects from the tail is how three replicas send three copies of every audit record. And it **resumes from a cursor the store defines** rather than from a position, because on PostgreSQL a position cursor loses events that commit out of allocation order. The cursor is ephemeral: it starts at the log head and dies with the pod, since a replica's view is rebuilt from a snapshot plus the tail on every start -- and the head is taken *before* the snapshot is read, so an event landing between the two is delivered rather than falling in the gap. A read failure does not stop the tail, and an event that cannot be applied does not stall it: a replica whose tail stopped would keep serving confidently from a frozen view. Verified against a real PostgreSQL with two independent replicas over one database: an event produced on A is delivered once on A and once on B, the SIEM receives one copy per event across the fleet, both replicas end with the same view, the log does not grow when a peer's event is applied, and a replica that joins late gets everything after it joined and none of the history before ([#801](https://github.com/mcp-hangar/mcp-hangar/pull/801))
- **core:** storage is one decision, and there are two separate backends to choose from. It used to be decided in two independent places -- `auth.storage.driver` and `event_store.driver` -- which nothing compared, so a deployment could keep API keys in PostgreSQL and its event log in a local file and look correctly configured from either end ([#779](https://github.com/mcp-hangar/mcp-hangar/issues/779)). The PostgreSQL side then covered API keys and roles and returned `None` for tool-access policies, silently disabling their management and their startup replay: a partial backend was expressible, so one shipped. `persistence.backend` now names a bundle of **every** persisted concern -- event log and delivery mark, server configuration, audit trail, saga state, approvals, API keys, roles, tool-access policies, metric history -- and the backend is refused unless it serves all ten, with the missing ones named. `sqlite` is the standalone answer and `postgresql` the multi-node one; they are separate implementations, not one with two modes, each owning its own driver and its own SQL with no dialect branch anywhere. Neither is privileged, and a third backend costs a package and an entry point under `mcp_hangar.persistence_backends`. Selecting a backend while a legacy driver key names a different one is a **startup refusal** rather than a precedence rule, because every precedence rule silently ignores half of what the operator wrote. **Omitting the block changes nothing**: every subsystem keeps configuring its own storage exactly as before ([#782](https://github.com/mcp-hangar/mcp-hangar/pull/782))

### Changed

- **core:** `subprocess` and `docker` servers are now refused in a deployment that shares its state with peers ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 4.1). Those modes do not describe a server the gateway talks to -- they describe one it *runs*: `docker run --rm -i` with stdin and stdout attached, held as a pipe inside one process. There is no address a peer could use, so a replica that learns about such a server and serves a call to it does not reach the existing copy; it starts **its own**, with its own child process and its own mounted volumes. Two writers to a store built for one, and a fleet whose answer depends on which replica the request reached -- and `_auto_add_volumes` hands a host path to any server whose name contains "memory", so the stateful case arrives by itself. That stopped being theoretical once a follower could learn about servers it had not registered, from the shared record at startup and from the tail immediately. Refused in two places on purpose: at **registration**, where the mistake is made and an operator can still act on it, with a message naming `remote` as the mode that works; and at **launch**, because a server can arrive from `config.yaml` or from a snapshot written before the rule, and by then the refusal has to be the one that holds. Both are asked per call rather than once at startup, so a lease lost mid-life stops the next start. A standalone gateway -- every deployment that has not selected a storage backend -- runs every mode exactly as before. Routing a follower's call to the holder was considered and rejected for now: it needs a peer-to-peer channel that does not exist, plus presence discovery, authentication between replicas and budgets across two hops, which is a larger piece of work than the whole of the rest of this phase ([#805](https://github.com/mcp-hangar/mcp-hangar/pull/805))
- **core:** the startup delivery sweep is now standalone-only, and effects follow the instance that produced the event ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 0.4). `dispatch_pending` reads the event log from one shared mark and hands everything past it to local handlers. That is right when this process is the only writer of that log and wrong the moment it is not, in both directions at once: it re-delivers *peers'* events to this instance's handlers -- a second SIEM export and a second cost record for work another replica already accounted for -- while the mark itself is advanced by whichever replica publishes next, so it moves past events a different replica appended and never delivered, and the sweep skips exactly what it exists to recover. Keying the mark per instance rescues neither direction: the instance identity is minted per process, so a row keyed by it is never found again after a restart and the sweep replays the entire log, while a key that is stable per replica leaves a replaced pod's backlog under an identity that never comes back, accumulating one dead row per rollout. So with a storage backend selected the sweep does not run, and it says so. The recovery has not disappeared, it has moved: a tool call happens on exactly one replica, so a replica exporting its own work and nobody else's is exactly-once by construction and needs no cursor at all. The residual exposure, stated rather than discovered, is an event appended by a pod that died before its handler ran -- a window of microseconds, since delivery is inline immediately after the append -- and that event is still in the log, so the gateway's own audit trail is complete either way ([#798](https://github.com/mcp-hangar/mcp-hangar/pull/798))
- **core:** a hangar cluster now requires PostgreSQL, and refuses to start without it ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790)). Several replicas on a file-backed backend do not collide: each pod gets its own file, grants itself its own lease -- the SQLite adapter always grants, correctly, because a file admits one writer -- runs its own management loops and holds its own fleet. They never disagree, because they cannot see each other, so every health check stays green while the deployment has as many fleets as it has pods. Confirmed on a real cluster before this existed: three replicas, all three answering `manages_fleet: true`, and the API also claiming `coordinates_with_peers: true` because a lease keeper existed -- a keeper is not coordination, a *shared* backend is. A `coordination:` block is now the statement that these replicas are meant to be one gateway, and it is refused on storage they cannot share, with a message naming both ways out. The question is asked on the axis the operator controls rather than by sniffing the environment: a thousand pods each with their own storage are a thousand gateways, which is a legitimate thing to run. A backend that cannot be shared no longer gets a lease keeper at all, since there is no peer for it to coordinate with, and `GET /api/system` reports `storage_is_shareable` alongside a `coordinates_with_peers` that is now true only when it is. A backend that has not declared itself shareable counts as unshared, because a backend whose adapters have not been examined for the question is not one to assume the answer for ([#808](https://github.com/mcp-hangar/mcp-hangar/pull/808))
- **core:** the configuration and audit repositories come from the selected storage backend too, which completes the set. They are built during `Runtime` construction, and the runtime is a frozen dataclass, so the storage decision now happens **before** the runtime is asked for rather than after -- selecting afterwards would have meant either mutating a frozen object or leaving these two on a different backend than everything else, which is the split this whole change removes. No SQLite `Database` handle is opened when a backend is selected: that handle exists to create the SQLite schema and a backend's adapters create their own. `RecoveryService` accepts `database=None` for the same reason, and now **refuses** the impossible combination -- no database *and* no repositories -- instead of trying to build SQLite stores from a handle that is not there; its repository parameters are typed as the ports rather than the concrete SQLite classes they happened to be. Recovery still runs, which is the part that would have been a silent regression: a gateway that starts empty and looks fine ([#786](https://github.com/mcp-hangar/mcp-hangar/pull/786))
- **core:** selecting a storage backend now actually moves the subsystems onto it. #782 introduced the decision and wired the event log and its delivery mark; auth, approvals and sagas still built their own stores beside it, which is this codebase's most reliable failure -- something correct, wired halfway, and silent about the half that is missing. Auth takes its API key, role **and** tool-access policy stores from the backend as a set; approvals take their repository; saga state comes from the backend instead of being keyed off the *event store's* driver, which was exactly the cross-subsystem coupling one storage decision removes. Metric history is installed too, and that uncovered a separate gap worth naming: `set_metrics_history_store` had **no caller anywhere**, so despite its own docstring saying production bootstrap should install a file-backed store, the default in-memory one has always been what production used -- metric history never survived a restart. Selecting a backend now installs it; without a selection nothing changes, and the pre-existing gap is unchanged rather than quietly fixed. The dead-symbol baseline tightens by one as a result (43 → 42) ([#784](https://github.com/mcp-hangar/mcp-hangar/pull/784))

### Fixed

- **core:** three defects found by deploying two replicas to a real cluster, none of which a unit test could see ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 4.4). **The shipped image had no PostgreSQL driver.** The Dockerfile installed the `kubernetes` extra and not `postgres`, so `persistence.backend: postgresql` -- the configuration that makes more than one replica possible at all, and the one the hardening guide recommends -- failed at startup on the one artefact where it is the supported answer. **The event store's schema was never created.** Seven of the eight PostgreSQL adapters create their tables when they are built; the event store keeps that as a separate `initialize()` and nothing called it, so a gateway ran with no `events` table. Nothing said so during startup, because nothing reads the log then -- what said so was the tailer, reporting `relation "events" does not exist` every two seconds into a log nobody was watching. **And a long outage was unreadable in both directions at once.** The tailer logged every failed read, thirty lines a minute per replica, which is the volume at which an operator stops reading; the lease keeper logged its failed acquisitions at `debug`, which at a production log level is silence -- so an instance that could not reach the store never became the manager and never said so, and a fleet with nothing converging it looked exactly like a fleet with nothing to do. Both now report the first failure, then one in thirty, then the recovery, with the length of the run in the message. Sixty seconds of a real outage went from thirty lines and no explanation to three lines that say what is wrong and what it means ([#806](https://github.com/mcp-hangar/mcp-hangar/pull/806))
- **core:** the shared circuit-breaker row now has one writer, and what is per-replica says so ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phases 3.1, 3.3 and 3.4). Each replica keeps its own circuit breaker in memory, deliberately -- sharing one would let a single pod with a network problem cut a healthy upstream off from the other two, trading a slow failure for an outage. But all replicas wrote the *same row* at shutdown, so a rolling update ended with whichever pod stopped last having overwritten the others, and the state restored on the next start was not the fleet's and not any replica's: it was the last one out's. That write is now the lease holder's, and the lease is released after it rather than before -- releasing first would have the leader stop being the leader a moment before doing the one thing only the leader may do. Lifecycle state stays local for the same reason as the breaker, and more bluntly: in subprocess mode each replica runs its own child process, so a shared state field would report READY for a process only one replica has. None of that is wrong, but none of it was visible either -- every number the API returned looked fleet-wide with no way to tell which replica had answered. `GET /api/system` now reports the instance that served the request, whether it coordinates with peers, whether it is the one currently managing the fleet, and that rate limits are counted per instance rather than across the fleet. That last one is the honest form of a limit that would otherwise multiply silently: with three replicas a configured 10 rps admits 30. Dividing by the replica count drifts exactly when it matters -- a rollout runs N+1 replicas and a failure runs N-1 -- and a shared token bucket puts a database round trip on the path of every call, so a fleet-wide limit belongs at the ingress, where the fleet has one entrance ([#803](https://github.com/mcp-hangar/mcp-hangar/pull/803))
- **core:** a server registered at runtime now survives a restart. `RecoveryService.recover_mcp_servers` reads a table of configurations on every start, and nothing wrote it: `save_mcp_server_config` had no caller outside a unit test, so a server registered through `POST /api/mcp_servers` or found by discovery lived only in memory and was gone when the process ended ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 0.3 of the HA work). The event log recorded that the registration had happened, which is what made the audit trail look complete while the fleet was not, and `/api/config` reported an empty set of persisted servers on a gateway that was running several. Registration, update and deregistration now write the configuration through a fleet writer wired to the selected storage backend -- and only to a durable one: with no backend selected the behaviour is exactly as before, because writing to the in-memory repository would make `/api/config` report a server as persisted that would still be gone. The write **waits and raises**: a registration whose durable half failed reports the failure rather than answering "created" and losing the server at the next restart, and it happens before the server joins the fleet, so a failure leaves nothing behind. One definition of the snapshot is now shared by registration and recovery, which previously built it separately -- two copies drift the first time a field is added, and the drift shows up only as a field quietly absent after a restart ([#794](https://github.com/mcp-hangar/mcp-hangar/pull/794))
- **core:** the fleet is read back on startup, and can be written at all. Two defects in the path [#794](https://github.com/mcp-hangar/mcp-hangar/pull/794) made durable, both found while wiring the next piece of the HA work. First: **nothing called recovery.** `RecoveryService.recover_mcp_servers` had exactly one caller, `bootstrap.runtime.initialize_runtime`, and that function has no callers at all -- so the snapshot written on every registration was never read, and a server registered through the API still did not survive a restart. Second, and worse: **nothing created the schema.** `Database.initialize()` had the same single dead caller, so on `persistence.backend: sqlite` the first registration failed with `no such table: mcp_server_configs`. That was invisible until #794, because until then nothing wrote to that table either -- making the write mandatory and loud is what surfaced it. The schema now arrives with the connection rather than from a call somebody has to remember, which removes the ordering question instead of answering it, and bootstrap restores the fleet after the event store is installed, so each restored server replays its own stream and comes back in the state it had rather than COLD. Configuration wins over the snapshot: a server declared in `config.yaml` is left alone, because the file is the operator's live intent and the row is a record of what was true last time. Also fixed while in there: the SQLite backend's `close()` called an async `Database.close()` and dropped the coroutine, so it closed nothing and said so only as a `RuntimeWarning` during shutdown ([#800](https://github.com/mcp-hangar/mcp-hangar/pull/800))
- **core:** a server declared in `config.yaml` no longer costs the gateway its storage backend. The runtime is a singleton and a frozen dataclass, so it takes the backend **at construction** and cannot be given one afterwards -- and building a server declared in configuration reaches for that singleton. Reading the configuration therefore constructed the runtime *before* the backend had been selected, and the backend never arrived ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 4.4). The result was a gateway that had selected PostgreSQL, said so in its logs, and then used the in-memory config repository for the rest of its life: registrations were not written down, the fleet projection was never wired, and recovery had nothing to read -- so every durable half of the multi-replica work quietly did nothing. The only trace was a single `fleet_writer_absent` line, which reads like a configuration choice rather than a defect. It happened whenever `config.yaml` declared at least one server, which is the ordinary case; an empty `mcp_servers:` block is what made earlier testing of this pass. Reading the configuration and building its servers are now two steps, with the backend selected in between. Found by deploying two gateways across **two Kubernetes clusters** with one shared database, where the follower never learned about anything the leader registered ([#807](https://github.com/mcp-hangar/mcp-hangar/pull/807))
- **core:** a deregistration decided under a management tenure that has since ended no longer lands. The sequence it closes ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 1.4): an instance holding the lease decides a discovered server has expired, then stalls -- a stop-the-world pause, a wedged disk, a partition. Its lease expires, a peer acquires it and re-registers the server, which is alive and well. The stalled instance resumes and issues its deletion. Its own lease keeper cannot save it, because the keeper was frozen too and the write goes out before its next tick; the per-cycle gate cannot either, because this is inside a cycle that had already started. So the check is now **inside the write**: a convergence loop's deregistration carries the tenure it was decided under and lands only if that tenure is still current, which the database rules on at the instant of the write. An operator's deletion is deliberately not fenced -- they are not a stale loop finishing, and fencing them would make `DELETE /api/mcp_servers/{id}` succeed or fail depending on which replica the load balancer picked. Marked by `provenance`, set by the construction path and never by a request, exactly as on registration. Along the way, discovery's deregistration now goes through the command bus like its registration already did: it used to stop the server and drop it from the in-memory fleet directly, so a discovered server's *departure* was the one fleet change nothing recorded -- no `McpServerDeregistered` in the log, and, since the fleet became durable, the row left behind, so the server came back at the next restart ([#797](https://github.com/mcp-hangar/mcp-hangar/pull/797))
- **security:** an approval decision made on one instance now releases the call held on another. The hold is a `threading.Event` in one process, and the wait watched only that -- so a call held on instance A while the approver's `POST /approvals/{id}/resolve` landed on instance B sat there until it timed out and then **failed closed**. The approver saw success, the caller saw a denial, and the record said approved: the record and the outcome disagreeing, silently, which is worse than plain unavailability ([#778](https://github.com/mcp-hangar/mcp-hangar/issues/778)). The wait now watches both sources -- the local hold, which still answers immediately and is the common case, and the approval record, which is what two instances share once a storage backend is selected. A pending record is not a decision and unreadable storage is not a decision: both leave the gate to time out and fail closed exactly as before, because a storage hiccup must not refuse a call on its own. The note in `hold_registry` calling multi-instance "a Cloud MVP concern" is gone with the tier it referred to, which ADR-010 retired ([#787](https://github.com/mcp-hangar/mcp-hangar/pull/787))
- **core:** the storage-backend selection no longer tries to mutate a frozen `Runtime`. #782 assigned the selected backend onto the runtime dataclass, which is `frozen=True` on purpose -- the runtime is assembled once and not written to afterwards -- so bootstrap raised `FrozenInstanceError` as soon as any configuration reached it. The backend now lives in the bootstrap holder beside the discovery orchestrator, which is where bootstrap-time singletons already go. Two smaller corrections ride along: the `ISagaStateStore` port declared `checkpoint(saga_type, state, last_position)` while the real contract -- passed by keyword at both call sites -- is `(saga_type, saga_id, state_data, last_event_position)`, so the port was a misreading rather than the implementation being wrong; and three driver returns typed `Any` are narrowed at the boundary instead of leaking out of functions that promise `bool` and `int` ([#783](https://github.com/mcp-hangar/mcp-hangar/pull/783))
- **core:** the PostgreSQL auth driver can be installed. The `postgres` extra declared `asyncpg` while the code imports `psycopg2` -- a name that appears in exactly one line of `src/`, a docstring -- so `pip install mcp-hangar[postgres]` installed a library nothing imports and left the driver unable to start ([#779](https://github.com/mcp-hangar/mcp-hangar/issues/779)). The extra now installs `psycopg2-binary`. At the same time the second, private copy of the connection factory inside `postgres_store` is deleted: there were two implementations of "pool psycopg2 connections", which is how a backend ends up configured differently depending on which door you came through. Auth's stores now depend on the shared `IConnectionFactory` port and call `get_connection()`, so **one place knows psycopg2** and the stores know only SQL -- the property that makes a second backend an adapter rather than a rewrite. Still true and tracked in #779: the PostgreSQL driver carries API keys and roles but **not** tool-access policies, so selecting it silently disables their runtime management and their replay at startup ([#780](https://github.com/mcp-hangar/mcp-hangar/pull/780))

### Security

- **security:** a suspended session is now refused by every replica, not only by the one that suspended it. The registry is a set in one process, so a session suspended by a detection rule on replica A was refused by A and **served by B and C** -- the block was avoided by retrying the request, which made it an enforcement decision any caller could walk past without knowing it existed ([#790](https://github.com/mcp-hangar/mcp-hangar/issues/790), phase 3.2). The same held for an operator suspending a session over `POST /api/sessions/{id}/suspend`: a 200 and a block on one pod out of three. The decision now travels as a `SessionSuspended` / `SessionUnsuspended` event and every replica applies it through a projection, which is the same path the tail already carries everything else on. Both write paths apply it locally **and** announce it: publishing alone would be tidier, and it fails silently if the projection is not subscribed -- the request would return success and block nothing anywhere. Applied first, the block always holds where it was decided; the event is what makes it fleet-wide, and re-applying it is a no-op because suspension is idempotent. The propagation window is one tail interval, which is the honest cost of not putting a database read on the path of every tool invocation; a suspension responds to behaviour that has already happened, so two more seconds of it is a better trade than that. Session events get their own stream, since they name no server and would otherwise be delivered and never stored -- while `DetectionRuleMatched` and `EnforcementActionTaken`, which name both a session and a server, stay in the server's history where the session is context rather than the subject ([#802](https://github.com/mcp-hangar/mcp-hangar/pull/802))

## [2.4.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.3.0...v2.4.0) (2026-08-05)

### Added

- **core:** discovery records what it did. Five event classes described exactly its work -- a server appeared, its definition changed, it was refused, it went away -- and nothing ever constructed one ([#762](https://github.com/mcp-hangar/mcp-hangar/issues/762)): the vocabulary shipped, the feature went live, and the log stayed empty. Four are emitted now (`McpServerDiscovered`, `McpServerDiscoveryConfigChanged`, `McpServerQuarantined`, `McpServerDiscoveryLost`) and land in the discovered server's own stream, so a history reads *discovered → registered → started* in one place. Two are declared unemitted **on purpose**, with the reason recorded next to them: at a 30s refresh a cycle event is 2880 rows a day per gateway saying nothing changed, and source health is a gauge that already exists. Three details that decide whether this is an audit log or noise: the discovery is recorded **before** registration is attempted, so the order matches causality and a server the control plane then refuses is still on record; a refusal is recorded on the **transition** only, because a refused server is re-reported and re-refused every cycle; and an unchanged re-sighting records nothing at all ([#775](https://github.com/mcp-hangar/mcp-hangar/pull/775))

- **ci:** a gate on handlers mutating the event they receive. `publish` hands every handler the same instance, in sequence, on one thread -- so a handler assigning to a field changes what later handlers see and, now that events are persisted, what gets written to the stream. Nothing enforced this. `frozen=True` on `DomainEvent` would, and is not free: Python refuses a non-frozen dataclass inheriting from a frozen one, so freezing the base freezes **every** subclass (85 decorators in `domain/events/` alone) and breaks any downstream that subclasses one with a plain `@dataclass` -- a wide, breaking change against a mutation that does not currently happen. The invariant is tested instead, at zero cost to callers, and the test pins its own detection so a refactor cannot leave it permanently green ([#765](https://github.com/mcp-hangar/mcp-hangar/pull/765))

- **ci:** a gate on events whose handler nothing feeds. `DetectionEnforcementHandler` -- which suspends sessions and stops servers -- is subscribed to `DetectionRuleMatched` in bootstrap, and nothing in `src/` constructs that event: the enforcement path is wired end to end except for the step that would ever start it. No test, type check or lint could see that, because each half is correct on its own. Four such pairings are now declared with their reason (`DetectionRuleMatched` and `BehavioralDeviationDetected` belong to the deliberately unshipped anomaly detection; `EgressBlocked` and `TaskInputRequired` outlived the code that emitted them), and a new one fails the build unless it is declared too. The 18 event classes with neither a producer nor a consumer are baselined the same way, so the vocabulary can shrink but not grow -- that list is how a codebase accumulates names for features nobody built ([#760](https://github.com/mcp-hangar/mcp-hangar/pull/760))

- **core:** a server that was degraded before a restart comes back degraded. Recovery rebuilt each aggregate from its configuration and threw away everything the stream knew, so every process restart handed out a free circuit-breaker reset -- the one thing an enforcement plane must not do quietly. `McpServer.restore_from_events` now replays the aggregate own stream during recovery. The split is deliberate and pinned by tests: **replayed** are state, health counters, invocation totals and last use; **configuration** (mode, command, image, endpoint, env, TTLs, thresholds) still comes from config, because the answer to "what should this be" is not in history; and the **live transport client is never restored** -- liveness is re-earned by connecting, never assumed from a record. Replay reads stored timestamps rather than stamping `time.time()`, so a week-old failure is not re-dated to now. Unknown event types are skipped rather than raising, so a stream written by a newer version cannot stop an older one from booting, and dispatch walks the MRO so a pre-rename `Provider*` event finds its modern handler. There is one aggregate, not two: the parallel `EventSourcedMcpServer` class deleted in #749 is not resurrected ([#759](https://github.com/mcp-hangar/mcp-hangar/pull/759))

- **core:** domain events are persisted. Every drain point -- the seven in `crud_handlers`, plus `_publish_events` in the command handlers, the mcp_server service and the GC worker -- appended nothing: they called `EventBus.publish`, which does not persist, and the only methods that do had no production caller. `data/events.db` was created on 2026-07-16 and held zero rows in all four tables. Those ten sites now append the aggregate batch to its stream and deliver it, so the store holds what the platform has been advertising. `publish_aggregate_events` is on the `IEventBus` port rather than only on the concrete bus, because naming the aggregate a batch belongs to is what the application layer now needs of a bus. Three things this could have got wrong and does not: a default `expected_version=-1` claims the stream does not exist, which holds exactly once per aggregate and would have raised `ConcurrencyError` inside a fault barrier on every second command -- appends default to the end instead, and a caller that does claim a version keeps its check; a failed append no longer stops delivery, because metrics, audit, security and enforcement all run off this path and a disk-full event must not silently switch off enforcement -- the events are delivered and the missing record is logged as a hole in the audit log; and a genuine `ConcurrencyError` is not degraded into that warning ([#758](https://github.com/mcp-hangar/mcp-hangar/pull/758))

- **core:** events that were stored but never delivered are delivered on the next start. `publish_to_stream` commits the append and then calls handlers; a process that died between the two left the events durably in the store with no handler having seen them, and nothing that would ever look again -- at-most-once, on the path the project describes publicly as an audit trail. Delivery is now recorded in a durable high-water mark that is advanced only after handlers have run, and a sweep at startup delivers whatever sits past it. No outbox table was needed: `events` already carries `global_position AUTOINCREMENT` and the store already reads by it, so the log is the outbox and the dual-write problem does not arise. Delivery semantics are unchanged -- handlers still run inline, in the publishing thread, before `publish_to_stream` returns. The contract is at-least-once, so **handlers must be idempotent on `event_id`**; recovery re-reads events from the store, which means they arrive as deserialized copies rather than the instances an aggregate emitted ([#757](https://github.com/mcp-hangar/mcp-hangar/pull/757))

- **core:** a dead-symbol gate. Five defects this release turned out to be code that could not run -- an adapter never constructed, a port never injected, a module with no callers, a fallback beside an injected dependency -- and every one was found by accident while chasing something else. `scripts/check_dead_symbols.py` asks the question on purpose: which public symbols does nothing reference? The answer is baselined in `pyproject.toml` and can only shrink, the same ratchet the complexity baseline and import-contract ledger use. Symbols exported through an `__all__` are counted separately, because deleting those is a release decision rather than a cleanup. Current: 45 unreferenced, 4 exported-unreferenced ([#737](https://github.com/mcp-hangar/mcp-hangar/pull/737))

### Changed

- **core:** a discovery source declares its own validation rules instead of the core recognising it by name. `SecurityValidator` branched on `source_type == "kubernetes"` and applied namespace rules held in the core security config -- so a security component knew which sources exist, the core config spoke Kubernetes to every operator including those not running it, and any other source passed that stage **vacuously**: the branch simply did not match, nothing was validated, and nothing said so. Sources now answer through an optional `policy_violation` hook, returning a reason and details in their own vocabulary; the core keeps the checks true of every source (rate, count, health, schema) and learns nothing about namespaces, projects or datacenters. The hook is optional deliberately -- an abstract one would break every existing third-party source. **Config move**: `discovery.security.allowed_namespaces` / `denied_namespaces` belong to the kubernetes source now; the old location still applies with a deprecation warning, because relocating a security setting silently is the one migration that must not be quiet. Defaults are unchanged ([#768](https://github.com/mcp-hangar/mcp-hangar/pull/768))

- **core:** a discovery source no longer costs a patch to the core. Adding one meant a branch in an `if/elif` in `server/bootstrap/discovery.py`, an entry in the adapter package, and the delivery layer knowing what `socket_path` or `label_selector` mean -- so the adapter, which is the only part that should have been work, was the small half. Source construction moves to `infrastructure/discovery/registry.py`, where each factory owns its own option names and the config dict is passed through untouched. Third parties register under the `mcp_hangar.discovery_sources` entry point group, mirroring what `entrypoint_source` already does for MCP servers. The `DiscoverySource` port is unchanged: three methods, and it was never the problem. **Behaviour change**: a configured `source_type` that nothing provides now raises at startup instead of being skipped with a warning -- a typo used to produce a gateway running with no discovery and one line in the log, and `init_event_store` already refuses an unknown driver this way. A missing optional dependency still degrades, because that is a deployment shape rather than a configuration mistake ([#766](https://github.com/mcp-hangar/mcp-hangar/pull/766))

- **ci:** changelog entries are written as one file per PR in `changelog.d/` instead of a line in a shared `## [Unreleased]` block, and `CHANGELOG.md` is assembled from them at release time. The old convention made every open PR write to the same anchor in the same file, so two PRs open at once conflicted by construction and the second to merge got a hand-resolve; release-please separately generated its own section above that block, which left the prose orphaned under the wrong heading -- v2.3.0 shipped after consolidating three of them by hand. release-please now runs with `skip-changelog: true` and keeps the version, the tag and the release PR; the fragments own the prose. Nothing about a released changelog changes for a reader: the assembled section carries the same headings, the same entries and the same PR links, and the historical file was normalized to that one format in the same pass ([#750](https://github.com/mcp-hangar/mcp-hangar/pull/750))

- **core:** the cost counter moved out of the application layer, and `CostReportGenerated` gained schema **v2**. The event carried tenant/period/total and none of the mcp_server / tool / cost_model dimensions `record_cost` needs, so the metrics adapter could not reconstruct the counter from it -- which is why `cost_handler` wrote a Prometheus metric directly, the only application module that did, and the last of three such entries in the import-contract ledger (now 11 → 9 across this and the session-suspension change). v2 adds those three plus `cost_cents`, carried rather than re-derived from `total_cost`: that field is a string of whole currency units, and reconstructing hundredths from it is a float round-trip that drifts on a counter. The change is additive with defaults, so a stored v1 row replays through the upcaster chain's passthrough; no upcaster is registered because none could help -- v1 rows genuinely lack the dimensions, and the metrics adapter skips them rather than emitting an empty-labelled series ([#746](https://github.com/mcp-hangar/mcp-hangar/pull/746))

- **core:** `MetricsEventHandler.handle` dispatches through a table instead of a 19-branch `isinstance` chain. The chain sat at the complexity ceiling carrying an explicit "split before extending" note, and the baseline may only shrink -- so adding the cost branch required the split rather than allowing it to be skipped. Dispatch walks the MRO, because `isinstance` matched subclasses for free and a dict does not: four live event types (`ProviderStarted`, `ProviderStopped`, `ProviderStateChanged`, `ProviderDegraded`) reach their handler that way. The complexity baseline drops from 15 functions to 14 ([#746](https://github.com/mcp-hangar/mcp-hangar/pull/746))

- **core:** `DomainEvent` is a `kw_only` dataclass, removing ~290 lines of identical boilerplate. The base was deliberately not a dataclass -- inherited fields with defaults would have forced every subclass field to have one too -- and the price was 99 event classes each carrying the same three-line `__post_init__` whose entire body was `super().__init__()`. Keyword-only fields sit outside that ordering constraint, so the base can now own its identity fields while every subclass keeps its positional signature unchanged. `DomainEvent.rehydrate` passes the stored identity through the constructor instead of assigning after it; the `None`-keeps-the-fresh-one convention stays, so its two call sites do not reimplement it. Equality is unchanged: `event_id` and `occurred_at` are `compare=False`, preserving the payload-only comparison that fell out of the base not being a dataclass -- widening it is a defensible change, but a separate one from deleting boilerplate ([#744](https://github.com/mcp-hangar/mcp-hangar/pull/744))

- **core:** session suspension has a port. `DetectionEnforcementHandler` reached the suspended-session store through a function-local `from ...server.api.sessions import _suspended_sessions` -- an application handler depending on the delivery layer, past the underscore into another module's private state, behind an import that hid the edge from a reader. It was the only application → delivery entry in the import-contract ledger, which drops from 11 to 10. The store was never route code: it is a bounded, TTL-expiring, thread-safe cache, so it moved to `infrastructure/session_suspension.py` behind `ISessionSuspensionRegistry`, and the handler is handed the same instance the HTTP routes use. The registry is a **required** constructor argument rather than an optional one defaulting to `None`: a forgotten wiring now fails at construction instead of silently inside the handler's fault barrier, where enforcement would have logged one line and done nothing ([#745](https://github.com/mcp-hangar/mcp-hangar/pull/745))

- **core:** the trusted-hosts allowlist has one source. `MCP_TRUSTED_HOSTS` was parsed independently in the REST router, the MCP ASGI endpoint and (with the fix above) the PRM helpers -- three copies of a security allowlist that have to agree and nothing making them. It now lives in `mcp_hangar.trusted_hosts` in the shared kernel, with a guard asserting no call site re-reads the variable. Behaviour and the default (`localhost,127.0.0.1,::1,testserver`) are unchanged ([#742](https://github.com/mcp-hangar/mcp-hangar/pull/742))

### Removed

- **core:** eight domain event classes are removed: `CatalogItemApproved`, `CatalogItemDeprecated`, `CatalogItemPublished`, `CatalogItemRejected`, `ToolSchemaChanged`, `ToolSchemaDriftDetected`, `BehavioralModeChanged` and `CapabilityDeclarationMissing`. Nothing in the tree ever constructed one and nothing ever handled one -- they were vocabulary for features that were never built, found by the producer/consumer gate. **This breaks only code that imports those names**, and such a handler has never been called, so deleting it changes no behaviour; no event store needs migrating, because no stream can contain what was never emitted. The upgrade guide has the list. Ten unemitted events stay on purpose: the five discovery ones (where the feature is live and the missing emitter is the defect), four quarantine ones and `PolicyPushRejected`, all of which touch the operator. The deserialization registry keeps a floor rather than a census -- coverage is derived from the class hierarchy by a separate test that needs no maintenance ([#760](https://github.com/mcp-hangar/mcp-hangar/pull/760)) ([#764](https://github.com/mcp-hangar/mcp-hangar/pull/764))

- **core:** the second event-store hierarchy is gone. `infrastructure/event_store.py` carried its own `EventStore` ABC, a second `ConcurrencyError`, a second `InMemoryEventStore`, a `FileEventStore`, an `EventStoreSnapshot` and a module-level singleton -- beside the port in `domain/contracts/event_store.py` and its adapters in `infrastructure/persistence/`. Two classes of the same name and two exceptions of the same name is a trap that fails silently: an `except ConcurrencyError` against the wrong import does not error, it simply does not catch. Nothing in `src/` had imported it since the wiring fix, so this is the removal half of that change. Its 24 unit tests go with it; the port-shaped stores carry 65 of their own. The `FileEventStore` and the snapshot machinery that ADR-002 named are part of what leaves -- they had no caller in four months ([#756](https://github.com/mcp-hangar/mcp-hangar/pull/756))

- **core:** `EventSourcedMcpServerRepository` and `EventSourcedMcpServer` (1741 lines including tests). Both arrived with the enterprise migration in April 2026, in the same commit as `EventSourcedApiKey` and `EventSourcedRoleAssignment` -- and unlike those two, neither was ever wired. In four months the repository was never constructed anywhere in `src/`; the aggregate's only consumer was that repository, so the pair formed a closed island nothing reached. **They were not broken and not untested** -- ~800 lines of tests covered them, including a snapshot-load-equals-full-replay equivalence check -- which is exactly why this is recorded plainly: the code worked, it simply had no caller and no one asked for one. Event sourcing for `mcp_server` remains available in history if it is ever wanted as a deliberate decision rather than an inherited artifact ([#749](https://github.com/mcp-hangar/mcp-hangar/pull/749))

- **core:** the event-sourced repository's global singleton and its two accessors. `get_event_sourced_repository` / `set_event_sourced_repository` had no callers anywhere in the tree, and the module-global they wrapped was reachable only through them, so all three went together. They were the lazily-constructed-singleton pattern the rest of the codebase does not use -- every other repository is injected -- which is presumably why nothing ever called them. Found by the dead-symbol gate added in 2.3.0; the baseline drops from 45 to 43 ([#743](https://github.com/mcp-hangar/mcp-hangar/pull/743))

### Fixed

- **core:** registering a server is written to the event log. There were two publish methods and only one kept a record -- `publish()` delivered and forgot, which its own docstring said, while `publish_to_stream()` appended first -- and 34 call sites used the forgetful one against 10 that did not. So `POST /api/mcp_servers/` answered `created: true` and wrote **zero rows**, and an aggregate's stream began with `McpServerUpdated`: an edit to something the log had no record of creating ([#772](https://github.com/mcp-hangar/mcp-hangar/issues/772)). "Which method should I call" is not a question a caller gets right reliably, and getting it wrong was silent, so the stream is now derived from the event itself: an event naming an aggregate (`mcp_server_id`, `group_id`) is appended to that aggregate's stream, one naming none is delivered as before. No call site changed. The `source` field rides along, so the log finally answers which door a server came through -- `api` for a REST call, `discovery:kubernetes` for a discovered pod. Handlers see each event exactly once, a store that cannot write still delivers, and the startup sweep still does not re-append what it reads ([#774](https://github.com/mcp-hangar/mcp-hangar/pull/774))

- **core:** the kubernetes discovery source is installable. Its client package was declared nowhere -- not a dependency, not an extra, not in the image -- so the source could not be constructed on any supported install: bootstrap took the `ImportError` branch, logged `discovery_source_unavailable`, and discovery for that source did nothing, while the guide, the cookbook and the configuration reference all described a working feature and CI skipped its tests for the same reason. Install it with `pip install mcp-hangar[kubernetes]`; the published image ships it by default, because the image is what runs in a cluster. `docker` is a base dependency, which is why that source always worked -- this gives kubernetes the same standing ([#770](https://github.com/mcp-hangar/mcp-hangar/pull/770))

- **core:** a handler that throws is now counted, not only logged. The fault barrier around event dispatch was right -- one bad handler must not stop the others -- but the only trace of a swallowed failure was a log line, so an audit handler raising on every single event looked exactly like one that was working. Handler failures increment `mcp_hangar_errors{component="event_handler"}` labelled with the exception type, and the log line now names the failing handler. `EventBus.on_error`, which existed for this and was **registered by nobody**, is removed along with the list it fed: the loop over it ran zero times on every failure, which is dead code in the one path that only runs when something is already wrong. This breaks only code that called `on_error` directly; the port `IEventBus` never declared it, and the barrier behaviour is unchanged ([#763](https://github.com/mcp-hangar/mcp-hangar/pull/763))

- **core:** the tool-invocation history endpoint returns rows. `GetToolInvocationHistoryQuery` read a different event store from the one bootstrap configures -- a lazily created in-memory singleton in the legacy `infrastructure/event_store.py` that nothing ever writes to -- and composed its stream id as `mcp_server-{id}` while the only writer composes `mcp_server:{id}`. Either fault alone guaranteed an empty answer. The id now has one source in the shared kernel, read by both halves, and `from_position` became inclusive: the previous form skipped position 0, so the default request dropped the first event of every stream. Nothing depended on that, because the query could not return a row at all. The same phantom store was handed to the `event_sourcing` auth driver, whose `EventSourcedApiKeyStore` calls `read_stream`, `get_stream_version` and `list_streams` -- three methods that class does not have, so the driver raised `AttributeError` on its first index build rather than merely being non-durable as documented. Both consumers are now handed the store `init_event_store` configured ([#755](https://github.com/mcp-hangar/mcp-hangar/pull/755))

- **core:** `CallbackSecuritySink` and `CompositeSecuritySink` are gone. Zero references anywhere in the tree, tests included -- only `LogSecuritySink` is ever wired, since `get_security_handler()` is always called with no sink. They came in with the enterprise migration and were never used ([#748](https://github.com/mcp-hangar/mcp-hangar/pull/748))

- **core:** the dead-symbol gate could not see through a package facade. `_referenced_names` counted every import alias as a use, so `from .module import Thing` in an `__init__.py` marked `Thing` referenced even when nothing in `src/` or `tests/` imported it -- which is how `CallbackSecuritySink` and `CompositeSecuritySink` sat with zero references anywhere while the gate reported a clean baseline. The scanner already excluded `__all__` *string* entries, so half the problem had been seen; the import that feeds them had not. It now keys on the same marker for both, which is also the marker ruff uses to decide an `__init__.py` import is a deliberate re-export rather than an unused one, so the two tools agree on what a facade is. The **exported-unreferenced** baseline grows from 4 to 38 as a result -- that bucket exists precisely for "deleting this is a release decision", and it was previously reporting almost none of them. The `unreferenced` bucket is unchanged at 43, so nothing private was being hidden ([#748](https://github.com/mcp-hangar/mcp-hangar/pull/748))

- **core:** nine `Group*` events no longer discard their identity on replay. They live in `domain/model/mcp_server_group.py` rather than the events package, and once the base became a dataclass their inherited `super().__init__()` call ran *after* the generated `__init__` had assigned the fields -- overwriting a restored `event_id` with a fresh uuid and a restored `occurred_at` with the time of the read. That would re-date history to whenever the stream happened to be replayed and break idempotency for any consumer keyed on event id. Caught by the event-serialization fuzz test during the refactor above; a new tree-wide guard now matches on the base class rather than on a directory, so an event defined outside the events package cannot reintroduce it ([#744](https://github.com/mcp-hangar/mcp-hangar/pull/744))

- **core:** mutating REST endpoints answer 400 instead of 500 on an incomplete body, and an inactive module answers 503 instead of 500. Five endpoints indexed the parsed JSON directly (`body["mcp_server_id"]`, `body["group_id"]`, `body["source_type"]` and two more); `KeyError` is not `ValueError`, so it escaped each route's handler into a generic "internal server error" -- telling the caller the server had broken when their request was merely incomplete, and filling the log with unhandled exceptions in exactly the channel that is supposed to stay quiet. The validation is one shared helper rather than five copies. Separately, `GET /api/auth/keys` with auth disabled reached a mounted route whose CQRS handlers are registered only when auth is enabled; the bus now raises a typed `HandlerNotRegisteredError` that maps to 503, which answers correctly for any module that is present but inactive. A security audit found one of the five endpoints (SEC-04) and the auth route (SEC-05) ([#741](https://github.com/mcp-hangar/mcp-hangar/pull/741))

### Security

- **security:** a discovered server now joins the fleet through the same door a requested one does. Discovery built the `McpServer` aggregate itself and called `repository.add`, bypassing everything `CreateMcpServerCommand` does on the way in: the duplicate guard, the **SSRF check on a remote endpoint**, and `McpServerRegistered`. With `auto_register` on by default, a discovery source could therefore add an unvalidated server -- including one pointing at link-local metadata -- leaving one log line and no record that it happened. Registration is routed through the command bus, and `source="discovery:<type>"` carries the provenance the CRUD path has always carried. The command gains `volumes` and `read_only`, which the aggregate has always accepted: without them this rerouting would have silently dropped a discovered container mounts, and `read_only` defaults to `True` to match the aggregate, so omitting it means "hardened", not "off" ([#767](https://github.com/mcp-hangar/mcp-hangar/pull/767))

- **security:** the SSRF check is now scoped by provenance, which makes discovery work again. Routing discovery through `CreateMcpServerCommand` (#767) also routed it through a check written for endpoints a *human* types, where every private address is the attack -- and a container or pod address is private by definition, so **every discovered container was refused** since that change ([#771](https://github.com/mcp-hangar/mcp-hangar/issues/771)). Provenance is a type set by the construction path, not the free-text `source` field, so a request body cannot claim it; it defaults to `HUMAN`, so a call site that says nothing gets the strict policy. And it grants a *specific address* rather than an address class: a discovered endpoint must resolve to an address the container runtime reported for that container or pod, which also refuses registration-time DNS rebinding and stops a container labelling itself with a neighbour's address. Link-local, the unspecified address and cloud metadata hostnames stay refused through every door, whatever a runtime claims -- and the rule is a denylist rather than an RFC1918 allowlist, because pod CIDRs are not guaranteed to be RFC1918 and `100.64.0.0/10` would have reproduced the same bug on such a cluster. Nothing changes for a human-supplied endpoint. Two smaller fixes ride along: the REST route matched the refusal by comparing the exception's message to one exact sentence, so a second refusal reason would have answered 500 instead of 400; and a discovery cycle logged "N registered" at INFO for a conflict-resolution outcome, contradicting the rejection warning on the next line ([#773](https://github.com/mcp-hangar/mcp-hangar/pull/773))

- **core:** a subprocess backend no longer opens a network port. The built-in default configuration -- what runs with no `config.yaml` -- launched the math example as a subprocess with no environment, and that example defaulted to `streamable-http` on `MCP_HOST`, which defaults to `0.0.0.0`. So a fresh install served MCP on `0.0.0.0:8080` with **no authentication, no rate limit, no audit trail and no L7 egress policy**: anyone who could reach the host could call the backend's tools around the gateway rather than through it. The same mismatch also meant the gateway itself could not talk to it -- the launcher speaks stdio, so every call failed with `startup_timeout` after 30 s and a fresh install could not invoke a single tool. Fixed in the launcher (a subprocess child now defaults to `MCP_TRANSPORT=stdio`, overridable), in the default config, and in the example. Reported by a security audit against 2.3.0 ([#738](https://github.com/mcp-hangar/mcp-hangar/pull/738))

- **core:** a role granted with a scope no authorizer collects is now refused instead of silently stored. `RBACAuthorizer._collect_roles` queries exactly `global` and `tenant:{id}`; a grant written with any other scope -- `*` being the tempting one -- was accepted, persisted, shown in the audit trail, and never matched. That fails closed, so it is not an escalation, which is exactly why it was easy to miss: an administrator who grants `*` believes a permission exists and has granted nothing, and the usual next move when a grant "does not work" is something blunter and less auditable. Validated in the domain and called from every store, so a store reached directly by the CLI, a migration or an embedder refuses it too. Reported by an independent model review during a security audit (LLM-02) ([#740](https://github.com/mcp-hangar/mcp-hangar/pull/740))

- **core:** a forged `Host` header can no longer become this resource's advertised identity. When `auth.oidc.resource_uri` is not configured, the RFC 9728 Protected Resource Metadata document and the `WWW-Authenticate` challenge derived their `resource` value from the request's `Host` -- a header the caller sets. Both paths are reached *before* any host check: Starlette's `add_middleware` prepends, so `AuthMiddlewareHTTP` wraps `TrustedHostMiddleware` and runs outside it, and the `.well-known` PRM endpoint on the serving app has no such middleware at all. An attacker could therefore make the document that tells clients where to send their tokens name a host of their choosing. An untrusted `Host` is now ignored rather than reflected, falling back to the first configured trusted host -- a value the operator chose, so a client that cannot reach it fails to authenticate instead of authenticating somewhere an attacker named. The `X-Forwarded-Proto` scheme is validated for the same reason. **Operational note:** a deployment serving on its own hostname must have it in `MCP_TRUSTED_HOSTS`; this was already required (`TrustedHostMiddleware` would otherwise reject the request outright), but an unlisted host now degrades the advertised identity rather than only the REST surface. Reported by an independent model review during a security audit (LLM-03) ([#742](https://github.com/mcp-hangar/mcp-hangar/pull/742))

## [2.3.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.2.1...v2.3.0) (2026-08-04)

Closes the last of the audit findings and pays the import-contract debt ledger
down from 33 entries to 11. Five real defects surfaced while doing that -- a
cold-start metric that was never published, an event store that could not read
back what it wrote under the event-sourcing auth driver, cost events delivered
to nobody, event-sourced aggregates outside the lock hierarchy, and a
`datetime` returning as a `str` on replay.

**One breaking change**, and only for code importing the concrete launchers from
the domain layer. It has warned since v1.0.2. The upgrade guide has the
before/after and a one-liner that lists affected call sites:
<https://mcp-hangar.io/docs/upgrade/>.

### Changed

- **core:** the secret redactor moved from `domain/security/` to the shared kernel. `logging_config` imported it from the domain -- the kernel reaching up -- and four layers speak it: the log processor, the domain aggregate and its egress policy, an application command handler and the approval service. It is a pure `str -> str` transformation over known token shapes with no I/O and no configuration, so it has nothing on the other side of it. A port was the alternative and would have been worse: an uninjected port means logging quietly stops redacting, which for this function is the worst available failure. `from mcp_hangar.domain.security import OutputRedactor, RedactionPattern` is unchanged ([#733](https://github.com/mcp-hangar/mcp-hangar/pull/733))
- **core:** `protocol.py` no longer reaches into the server layer. Deciding whether Hangar may forward a caller's Tasks declaration upstream requires knowing whether the governed relay is actually serving, and the leaf protocol module answered that by importing `server.context` and reading `ctx.governed_task_store` -- three layers up, and the largest single jump in the import-contract debt ledger. The relay's one activation seam now records the fact where the protocol layer can read it, in the same statement group that publishes the store, so the two cannot disagree; `reset_context()` clears both together ([#732](https://github.com/mcp-hangar/mcp-hangar/pull/732))
- **core:** both sagas require their saga manager instead of reaching for the global singleton. `McpServerRecoverySaga` and `McpServerFailoverEventSaga` took an optional `ISagaManager` and fell back to `get_saga_manager()` when none was given -- the application layer importing infrastructure for a dependency bootstrap already hands it. As with the reload handler, the fallback never ran in production and the tests took it: 34 of them constructed a saga without a manager, so the covered path and the shipped path were different ones. The argument is now required and keyword-only, and the tests pass it explicitly. Two more lines leave the import-contract debt ledger ([#731](https://github.com/mcp-hangar/mcp-hangar/pull/731))
- **core:** the configuration-reload handler goes through its port. `IConfigLoader` and `ServerConfigLoader` exist, in their own words, so the handler can "load and apply configuration without importing server-layer symbols from the application layer" -- and the handler kept an optional loader with a fallback branch that imported `server.config` directly, which held the very import the port was built to remove. Bootstrap has always injected the adapter, so that branch never ran in production; the handler's own tests, however, constructed it *without* a loader, so the tested path and the shipped path were different ones. The loader is now required and keyword-only, the tests exercise the production path, and `ServerConfigLoader` declares the ABC it previously only matched by shape -- nothing had been checking the two agreed, so a rename on either side would have surfaced as an `AttributeError` partway through a live reload ([#729](https://github.com/mcp-hangar/mcp-hangar/pull/729))
- **core:** removed `PersistentAuditStore` and its factory, 253 lines with zero references anywhere in the codebase -- not constructed, not exported from the package, not tested, not documented. It carried an injected `IAsyncTaskSubmitter` port that nothing ever supplied, so the only live path was its fallback to the global executor singleton, and that was the application layer's remaining import of `infrastructure.async_executor`. Also removed the dead `get_event_store()` fallback in the tool-invocation-history query handler: bootstrap already injects the store, so the branch could not run in a bootstrapped process while hiding a missing wiring if it ever could. The store is now a required argument, so a missing wiring fails at registration rather than at the first query. Two more lines leave the import-contract debt ledger ([#728](https://github.com/mcp-hangar/mcp-hangar/pull/728))
- **core:** `MetricsEventHandler` moved from `application/event_handlers/` to `infrastructure/observability/`. Its docstring describes it as bridging domain events to Prometheus, which makes it an adapter rather than an application service; it was the reason the application layer imported the metrics module. One more line leaves the import-contract debt ledger ([#727](https://github.com/mcp-hangar/mcp-hangar/pull/727))
- **core:** `lock_hierarchy` moved from `infrastructure/` to the shared kernel, next to `logging_config` and `errors`. It has no outward-facing counterpart -- it is an in-process ordering discipline whose `LockLevel` enum enumerates every layer from domain aggregates to client I/O -- and three layers already spoke it. The domain now imports it directly instead of through a runtime import, and three more lines leave the import-contract debt ledger. `from mcp_hangar.infrastructure import TrackedLock, LockLevel` is unchanged ([#726](https://github.com/mcp-hangar/mcp-hangar/pull/726))
- **core:** the launcher port describes a transport instead of naming two of them. `domain/contracts/launcher.py` declared `LaunchResult = StdioClient | HttpClient` -- a domain contract importing its own adapters, so a third transport could not be added without editing the domain. It also did no work: the aggregate held the launched client as `Any | None` with the real type in a comment beside it, so nothing was checked where it mattered. `LaunchResult` is now a `TransportClient` protocol carrying the three methods the domain actually uses, the aggregate is typed by it, and two more lines leave the import-contract debt ledger ([#725](https://github.com/mcp-hangar/mcp-hangar/pull/725))
- **core:** the batch call path is a chain of named gates rather than one 454-line function. `_execute_call_inner` was the largest complexity suppression in the codebase (CC=36) and the function every batch tool call goes through: cancellation, global timeout, target resolution, tool-access policy, withdrawal, digest pin, circuit breaker, validators, approval, cold start, deferred pin. Each is now a method returning a refusal or `None`, and their order -- which decides *which* refusal a caller receives, and therefore what it does next -- is a single readable list instead of 450 lines of sequence. A new test arranges two gates to fail at once and asserts which one answers, for every pair that can be co-triggered; before this only one such pair was covered, and that test caught an ordering coupling the split itself introduced ([#723](https://github.com/mcp-hangar/mcp-hangar/pull/723))
- **core:** `domain/events.py` is now a package. The single 2197-line module held 108 event classes spanning every bounded context at once, so any change to it touched a file 141 other modules import. It is split into thirteen context modules -- lifecycle, invocation, tasks, health, discovery, auth, operations, administration, enforcement, analysis, approvals, interceptors -- plus the legacy aliases, all re-exported from `__init__` so no import path changes. A guard test asserts the re-export surface covers every class defined under the package, because a forgotten re-export otherwise surfaces as an `ImportError` in production and as a silently missing type in the event serializer's class map ([#710](https://github.com/mcp-hangar/mcp-hangar/pull/710))
- **core:** the last eighteen event classes carrying a hand-written `__init__` now use the shared alias decorator, which grew to cover the second rename it did not know about (`provider_name` -> `mcp_server_name`, five discovery aliases) alongside `provider_id` -> `mcp_server_id`. Three deliberate behaviour changes, each verified against a before/after probe of every constructor path on all 111 event classes: **(1)** passing both spellings of one field with *conflicting* values now raises everywhere -- previously the `*_id` family raised and the `*_name` family silently kept the legacy value and discarded the modern one; identical values are still accepted, and no call site passes both. **(2)** the five `Provider*` discovery aliases now take the same positional arguments as the classes they alias; their own `__init__` started with `provider_name`, one slot ahead of the base's parameters, so `ProviderDiscovered(a, b, c, d)` and `McpServerDiscovered(a, b, c, d)` assigned three fields differently -- nothing called them positionally, which is the only reason it never bit. **(3)** `approvals/service.py` now uses the modern keyword in its four event constructions; mypy could not see the deprecated spelling while `**kwargs` swallowed it ([#712](https://github.com/mcp-hangar/mcp-hangar/pull/712))
- **core:** the ten `Provider*` alias events keep the legacy `provider_id` keyword through one decorator instead of ten hand-written constructors. The field assignment, required-argument check and unknown-keyword `TypeError` come back from the dataclass machinery, so they can no longer drift between classes -- which they had: three aliases had stopped accepting the modern `mcp_server_id` spelling entirely. `domain/events.py` loses 170 lines ([#708](https://github.com/mcp-hangar/mcp-hangar/pull/708))

### Removed

- **core:** removed the deprecated launcher import paths. `mcp_hangar.domain.services.mcp_server_launcher` and the launcher re-exports on `mcp_hangar.domain.services` (`DockerLauncher`, `SubprocessLauncher`, `ContainerLauncher`, `HttpLauncher`, `ContainerConfig`, `McpServerLauncher`, `get_launcher`) both raise now. Import them from `mcp_hangar.infrastructure.launchers`, which is where they live and what the `DeprecationWarning` has said since v1.0.2 -- a warning that survived the 2.0 major. The port, `IMcpServerLauncher`, is still exported from `domain.services`; it is the concrete classes that left. This also broke a real import cycle: the domain reaching for the concrete launchers is what forced two sagas to import their saga manager inside a function body. Eight lines leave the import-contract debt ledger ([#730](https://github.com/mcp-hangar/mcp-hangar/pull/730))

### Fixed

- **core:** cost-attribution events reached no handler. `CostAttributionEventHandler` called `event_bus.publish([cost_event])` -- a list, not an event. Dispatch keys on the event's type, so the list matched no specific handler and every `subscribe_to_all` handler (audit, logging, metrics) received the **list object itself**, where each one's `isinstance` chain quietly matched nothing; anything subscribed to `CostReportGenerated` was never called at all. Nothing failed, the event simply never arrived. The unit test covering it asserted `len(published_events) == 1`, agreeing with the bug because a mock bus never has to route anything. `publish()` now raises `TypeError` on a non-event rather than delivering to no one ([#727](https://github.com/mcp-hangar/mcp-hangar/pull/727))
- **core:** every event-sourced `McpServer` was outside the global lock ordering. `EventSourcedMcpServer.__init__` assigned a bare `threading.RLock` instead of the tracked lock its parent aggregate uses, unconditionally, so `LockOrderViolation` could never fire for one -- a deadlock involving an event-sourced aggregate would simply hang instead of raising. Surfaced by giving `McpServer._create_lock` a real return type. The two other aggregates and the repository built their locks inside a `try/except ImportError` that fell back to the same untracked lock; that branch could never run, so it guarded against nothing while advertising a silent downgrade ([#726](https://github.com/mcp-hangar/mcp-hangar/pull/726))
- **core:** cold-start metrics are published again. `mcp_hangar_mcp_server_cold_start_seconds` -- described in `metrics.py` as the critical UX metric, time from a request to a ready backend -- was never observed in production. The domain publishes it through the `IMetricsPublisher` port and the Prometheus adapter behind that port exists, but nothing ever connected the two: `PrometheusMetricsPublisher` appeared exactly once in the codebase, at its own `class` statement, so every `McpServer` fell back to the Null object. The histogram and its in-progress gauge had been empty series since the port was introduced. The composition root now installs the adapter, and `set_connection_active` moved onto the port as well, which removes the `domain.model.mcp_server -> metrics` edge from the import-contract debt ledger ([#724](https://github.com/mcp-hangar/mcp-hangar/pull/724))
- **core:** the event store could write events it could not read back. `EventSerializer.serialize` accepts any domain event -- it dumps the instance dict -- while `deserialize` looked the class up in a hand-curated table that listed 30 of the 116 event classes in the codebase. Under `auth.storage.driver: event_sourcing`, **every API key and role assignment was durably written and permanently unreadable**: all five events those aggregates emit (`ApiKeyCreated`, `ApiKeyRevoked`, `KeyRotated`, `RoleAssigned`, `RoleRevoked`) were missing from the table, so the next process to open the database raised `EventSerializationError` on its first read -- every credential stopped working across a restart, and revocations did not survive one either. The nine group events were in the same position, which is what the never-called `register_event_type` helper existed to solve. The registry is now derived from the event class hierarchy and refreshed on a lookup miss, so the reader accepts exactly what the writer produces ([#721](https://github.com/mcp-hangar/mcp-hangar/pull/721))
- **core:** a `datetime` field on a persisted event came back as a `str`. JSON has no datetime, so serialization wrote `isoformat()` and nothing parsed it back -- silently, and only on replay, so a consumer comparing or doing arithmetic on the value broke long after the write. Affected `PolicyPushRejected.timestamp`; the round-trip suite now asserts every field of every registered type comes back with its original type and value, rather than only that the event's class matches ([#721](https://github.com/mcp-hangar/mcp-hangar/pull/721))
- **core:** events written before the `provider` -> `mcp_server` rename now reach their handlers again. The rename landed after v1.0.1, so event stores from any of the eight earlier releases hold rows typed `ProviderStarted`, `ProviderDiscovered` and so on -- and replaying one was a silent no-op for every consumer. Two layers dropped them independently: the serializer resolved those type names to the deprecated alias *classes* and looked their schema version up under a key no upcaster is registered against, and the event bus dispatched on the exact class, so a `ProviderStarted` -- a subclass of `McpServerStarted` -- matched none of the handlers registered against the modern class. No error and no warning, just a `handlers_count=0` debug line. Legacy type names now resolve to the current class and version key, new writes use the current name, and bus dispatch walks the class hierarchy so a subclass event reaches base-class handlers exactly once ([#713](https://github.com/mcp-hangar/mcp-hangar/pull/713))

### ⚠ BREAKING CHANGES

- **core:** `mcp_hangar.domain.services.mcp_server_launcher` and the launcher re-exports on `mcp_hangar.domain.services` are removed. Import DockerLauncher, SubprocessLauncher, ContainerLauncher, HttpLauncher, ContainerConfig, McpServerLauncher and get_launcher from `mcp_hangar.infrastructure.launchers` instead. Run your suite with `python -W error::DeprecationWarning` to list affected call sites.

## [2.2.1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.2.0...v2.2.1) (2026-08-03)

### Changed

- **core:** replaying a persisted stream restores the event's stored identity through one seam, `DomainEvent.rehydrate`, instead of two modules patching `event_id` and `occurred_at` in place after construction. Nothing asserted that identity survived replay: a fresh `event_id` silently reprocesses everything for any consumer de-duplicating on it, and a fresh `occurred_at` re-dates history to whenever the stream was read ([#704](https://github.com/mcp-hangar/mcp-hangar/pull/704))
- **core:** the hexagon layering is enforced by `import-linter` instead of by review. Five layers, bottom-up: shared kernel < domain < application < infrastructure < delivery, with the 14 root-level modules split between the kernel and the infrastructure tier rather than lumped together -- folding them all into the kernel would have legalised `domain -> metrics` and `domain.contracts.launcher -> http_client`, a port importing its own adapter. 33 existing edges are baselined in a capped ledger; `tests/unit/test_import_contracts.py` guards the contract file itself, because `lint-imports` exits 0 on an empty one ([#703](https://github.com/mcp-hangar/mcp-hangar/pull/703))
- **core:** CI and developers now lint with the same ruff. The version was pinned twice -- `RUFF_VERSION` in `ci-core.yml` at 0.14.13, and `ruff>=0.3.0` in the dev dependencies, an open floor that resolved to whatever was newest locally. Rules that fired on a developer's machine were therefore invisible in CI; two `UP042` findings sat unseen for exactly that reason. The dev dependency is now pinned and is the single source, CI installs from it, and dependabot bumps it ([#702](https://github.com/mcp-hangar/mcp-hangar/pull/702))
- **core:** `ToolAction` and `PolicyMode` become `StrEnum`. Wire values are unchanged (`allow`/`deny`/`require_approval`, `Audit`/`Enforce`) and are pinned by a test, since the operator compiles `MCPEgressPolicy` objects against exactly those strings. `str()` on a member now yields the value rather than `ToolAction.DENY`; nothing relied on the old form ([#702](https://github.com/mcp-hangar/mcp-hangar/pull/702))
- **core:** branch coverage is now gated per module on the decision paths. `coverage.py`'s `fail_under` is global-only, so a single threshold would have to be low enough for the weakest module in the tree -- exactly the modules needing the strongest guarantee. 29 modules (authz, consent/approvals, egress + tool-access policy, digest) carry floors set to their MEASURED value, checked by `scripts/check_decision_coverage.py`. `branch = true` moves into `[tool.coverage.run]`, since a floor compared against statement-only data silently passes a lower bar, and mixing modes raises `DataError` ([#697](https://github.com/mcp-hangar/mcp-hangar/pull/697))
- **core:** cyclomatic complexity is now a gate. `C901` caps new code at 15; the 16 functions already above it carry an explicit `# noqa: C901 -- baseline CC=N`, and `tests/unit/test_complexity_baseline.py` caps that list so it can only shrink. Complexity had never been measured -- the ruff config omitted `C90` entirely, and the worst function in the tree scores 49 ([#696](https://github.com/mcp-hangar/mcp-hangar/pull/696))

### Fixed

- **core:** the decision-path coverage floor for `server/tools/batch/executor.py` drops to 84.5. The module dispatches work on a thread pool with timeouts and single-flight de-duplication, so a few branches fire or not depending on scheduling: measured 85.38 three times on CPython 3.13, 85.06 twice on 3.11, and 85.06 then 84.75 on two CI runs of the same tree. The floor now sits under the lowest observed CI value rather than under a reproducible local one, so the gate reports a real regression instead of the runner's mood. The job also uploads `coverage.json` on failure, so the next divergence is diagnosable rather than guessed at ([#699](https://github.com/mcp-hangar/mcp-hangar/pull/699))
- **core:** `ProviderRegistered`, `ProviderUpdated` and `ProviderDeregistered` rejected the modern `mcp_server_id` keyword -- they accepted only the pre-rename `provider_id`, unlike the other seven `Provider*` aliases which take both. Passing `mcp_server_id` raised `TypeError: Missing required argument: mcp_server_id` while the caller had supplied exactly that. The legacy alias contract is now pinned across every event that carries it ([#705](https://github.com/mcp-hangar/mcp-hangar/pull/705))
- **core:** authorization denials return the API's standard error envelope again. 2.2.0 moved authorization into middleware, where the `AccessDeniedError` no longer reaches `error_handler`, and the middleware reused the authentication layer's flatter body -- so a `403` changed from `{"error": {"code": "AccessDeniedError", ...}}` to `{"error": "access_denied", ...}`. Any client reading `error.code` broke. Caught by the nightly live-verify suite; now pinned by unit tests so the next regression fails in seconds rather than overnight ([#707](https://github.com/mcp-hangar/mcp-hangar/pull/707))
- **core:** `application/mcp` and `bootstrap` shipped without an `__init__.py`. The modules were tracked, the marker was not, so the 2.2.0 wheel carries them inside implicit namespace packages. Imports resolve either way, which is why nothing broke -- but static import analysis walks the package tree and skips a directory with no marker, so those modules were invisible to it ([#703](https://github.com/mcp-hangar/mcp-hangar/pull/703))
- **core:** three modules read `__version__` off the package root, pulling the entire public API -- facade included -- into an adapter, a health probe and the tracing bootstrap just to format a version string. They now read the installed distribution directly ([#703](https://github.com/mcp-hangar/mcp-hangar/pull/703))
- **core:** the decision-path coverage floor for `server/tools/batch/executor.py` was measured on CPython 3.13 but enforced on 3.11, where branch-arc counts differ (85.38 vs 85.06) -- so the gate failed on its first CI run. Floors now come from the Python the gate runs on, and the config says so ([#699](https://github.com/mcp-hangar/mcp-hangar/pull/699))

## [2.2.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.1.1...v2.2.0) (2026-08-02)

Closes the REST authorization holes found by an audit against the project's technical-quality requirements, plus four fail-open defects found alongside them.

**This is a minor, not a patch, on purpose.** Three of the changes below break a working deployment and two break it silently, and a patch would have been pulled in unattended by `~=2.1.1`-style constraints. Read [the upgrade guide](https://mcp-hangar.io/upgrade/#upgrade-to-220) before rolling out: an operator API key on the `developer` role stops delivering egress policy, an OPA policy returning a non-boolean flips from allow-all to deny-all, and a misspelled `tool_access.mode` now refuses to start.

### Added

- **core:** `EgressPolicySet` and `EgressPolicyCleared` domain events. `SetL7PolicyHandler` took an event bus and never published to it, so changing what the enforcement plane blocks left no audit trail ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))

### Fixed

- **core:** the event-serializer round-trip test now covers every registered type. It was `@given(sampled_from(EVENT_TYPE_MAP))` with `max_examples=17` against a 42-entry map, so a single run exercised at most 17 types and which ones depended on the seed -- a type registered without a sample passed on some runs and failed on others. It is now parametrized over all types, plus a check that every registered type has one ([#694](https://github.com/mcp-hangar/mcp-hangar/pull/694))
- **core:** register `EgressPolicySet` and `EgressPolicyCleared` with the event serializer. Both were added as audit events for egress-policy changes but omitted from `_EVENT_CLASS_BY_TYPE`, so `deserialize` raised `EventSerializationError` on them -- the record was written and then unreadable, which is not an audit trail. Their siblings `EgressBlocked` and `EgressPolicyViolationObserved` were already registered ([#694](https://github.com/mcp-hangar/mcp-hangar/pull/694))
- **core:** the authorization chokepoint resolves the route against `root_path`. Starlette does not rewrite `scope["path"]` under a `Mount`, and the served application mounts the REST router at `/api`, so a table keyed on the raw path matched nothing and -- the default being deny -- would have rejected every REST call on the app `serve --http` serves ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))

### Security

- **core:** authorize the REST/WebSocket surface from one route-driven chokepoint. Only `mcp_servers.py` and `admin_tools.py` ever called the per-handler guard, so `/config`, `/discovery`, `/groups`, `/sessions`, `/tools`, the `/approvals` reads and the whole `/auth` subtree -- API-key minting, role assignment, tool-access policy -- were authenticated but made no authorization decision. Any principal holding any valid credential, including the operator's `X-API-Key`, could `POST /api/auth/roles/assign` and grant itself `admin`; the shipped Helm charts render no Ingress, so nothing fronted the API. Authorization is now resolved from the route via a declarative table, and a route absent from it is denied, so a new endpoint is unreachable until someone decides who may call it ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))
- **core:** `/mcp_servers/{id}/l7_policy` now requires `policy:write`, not `mcp_servers:write`. It is the operator's channel for compiled `MCPEgressPolicy` objects (ADR-013), and `mcp_servers:write` is held by `developer`, so a developer token could clear a compiled egress policy. `policy:write` was defined, granted to admin only, and enforced nowhere. **Breaking:** `provider-admin` gained `mcp_servers:read` + `policy:write` and is the least-privilege home for an operator key; an operator running a `developer` key stops delivering policy, silently -- the CRD still reports `Compiled` ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))
- **core:** `POST /api/config/reload` requires `config:reload` and no longer accepts a caller-supplied `config_path`. Reload loads whatever path it is given and an `mcp_servers` entry carries `command`/`args`, so the old behaviour was a remote "load an arbitrary file and start what it describes" primitive. A request still sending the field now gets `422` rather than being silently ignored ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))
- **core:** OPA's verdict is required to be a boolean rather than merely truthy. Rego rules commonly return an object or a string, and every such shape was read as allow -- including `{"result": "deny"}`, which granted access while saying the opposite. A non-boolean verdict is now a denial, and a missing `result` key (an undefined rule, e.g. a wrong `policy_path`) is reported distinctly. **Breaking:** a policy returning anything but a bare boolean flips from allowing everything to denying everything ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))
- **core:** a partial tool-access-policy update no longer drops the consent gate. `SetToolAccessPolicyCommand` carries only allow/deny lists, so rebuilding the policy from it discarded `approval_list`, `approval_timeout_seconds` and `approval_channel` -- and the enforcement path reads the same resolver, so a plain "add one deny pattern" call silently un-gated every tool requiring human approval ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))
- **core:** approval arguments are redacted by value, not only by key name, so a secret under an innocuous key no longer reaches the SQLite record or the REST DTO. The dispatch-time integrity hash moved to the raw arguments, because two different secrets redact to the same marker and would otherwise hash identically. **Breaking:** approvals pending across the upgrade fail revalidation and must be re-requested ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))
- **core:** a misspelled `tool_access.mode` stops the server instead of resolving to `egress` with a warning, which handed a deployment that intended `front_door` the permissive topology. An absent key still means `egress`. **Breaking** for a config with a typo in that key ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))
- **core:** an unknown `arguments.secretPatterns` group is rejected when the L7 policy is parsed. It used to be skipped at scan time, and the docstring deferred to CRD validation that does not exist -- the CRD declares the field as a plain string array with no enum -- so a misspelled group left the policy reporting as enforcing with that detector off ([#692](https://github.com/mcp-hangar/mcp-hangar/pull/692))

## [2.1.1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.1.0...v2.1.1) (2026-08-01)

Two security fixes from red-teaming the 2.1.0 release on a live cluster. Both are fail-closed and confined: the JWT change only widens detection, and the tenant scoping is inert outside multi-tenancy.

### Security

- **core:** the L7 egress `jwt` secret pattern now catches short-header JWTs. It required 50+ base64url chars in the header segment, but a standard header -- `{"alg":"HS256","typ":"JWT"}` -- is ~33, so every HS256 token and anything without a `kid` evaded the detector while only long-header RS256 tokens matched. A JWT exfiltrated through a tool-call argument slipped past. The pattern now matches the two/three-segment JWT structure with realistic per-segment minimums, verified on a live gateway (the token that was allowed through is now blocked) ([#687](https://github.com/mcp-hangar/mcp-hangar/pull/687))
- **core:** scope the approval surface by tenant. Resolution and listing authorized on the `approval:resolve` permission alone, ignoring the caller's tenant, so an approver in one tenant could list and resolve high-risk approvals raised in another -- and read their arguments. An approval now binds the tenant that raised it; resolve and list/get are scoped to the caller's tenant, and a foreign-tenant approval is reported as not-found rather than forbidden so its existence is not disclosed. An approval with no tenant (single-tenant / auth-off) is unscoped, so the change is inert outside multi-tenancy. `tenant_id` and `requested_by` are now persisted (SQLite migration included). Verified live: a `tenant:b` approver that previously saw and resolved a `tenant:a` approval now sees zero and gets 404 ([#688](https://github.com/mcp-hangar/mcp-hangar/pull/688))

## [2.1.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.0.1...v2.1.0) (2026-08-01)

The human-in-the-loop approval gate becomes reachable for the first time. Until now no configuration could put a tool behind it, the gate service was never constructed, and the REST surface answered 500 — a governance control that was documented, tested in isolation, and unreachable in every shipped build. A minor rather than a patch because it adds config keys; nothing that worked before behaves differently.

### Added

- **core:** the human-in-the-loop approval gate is reachable. It was not — on any shipped path, for as long as it has existed ([#678](https://github.com/mcp-hangar/mcp-hangar/pull/678)). Three independent breaks, each confirmed against a running container. **No config surface:** `approval_list` existed on `ToolAccessPolicy` and in its own unit tests, and in no YAML, REST or CLI input, so nothing could put a tool behind approval in the first place. **No service:** `bootstrap_approvals()` had no call site anywhere in `src/`, and the loader that would have called it returned early whenever auth was absent — the default — so `ServerComponents.approval_service` stayed `None`, the `ctx.approval_gate = ...` assignment guarded by it never fired, and a call the policy said to hold executed immediately with a `approval_gate_not_configured` debug line and a real result. **No route:** `/api/approvals` read `app.state.approval_gate_service`, set at exactly one call site from a field that was never populated, and answered **500** with `AttributeError: 'State' object has no attribute 'approval_gate_service'`. A `tools:` block now accepts `approval_list`, `approval_timeout_seconds` and `approval_channel` everywhere it already accepted `allow_list`/`deny_list` — mcp_server, group, group member, and the per-tenant `tool_access.member` block — through one shared parser, so a key cannot be honoured at one scope and silently dropped at another (it was copy-pasted five times, which is how this diverged). The gate service is built in `load_components` independently of auth, and `create_api_router` publishes it onto `app.state` so the HTTP-serve path, `MCPServerFactory` and any test client are wired identically rather than at one call site; the routes also fall back to the application context — the same object the batch executor reads — so the API and enforcement can never hold different services. An absent gate is an explicit **503**, not a stack trace. Approvals are on by default and inert until a policy gates a tool; `approvals.enabled: false` opts out. The [#673](https://github.com/mcp-hangar/mcp-hangar/issues/673) dispatch-time revalidation shipped in 2.0.1 was the guard that had to be in place first; it now guards a path a deployment can actually enter
- **core:** a startup check that every subsystem the configuration asks for is reachable on the path this process took. This is the sixth instance of one class — the governed task relay ([#592](https://github.com/mcp-hangar/mcp-hangar/pull/592)), the request mutators ([#594](https://github.com/mcp-hangar/mcp-hangar/pull/594)), the SEP-2133 governance descriptors ([#595](https://github.com/mcp-hangar/mcp-hangar/pull/595)), the flat-tool projection ([#596](https://github.com/mcp-hangar/mcp-hangar/pull/596)), the operator's REST prefix (operator#91), and now the approval gate — where a subsystem was wired on one construction path while the shipped process used another, and in every one of them the process started clean and did less than its configuration said. The check runs at the end of `bootstrap()`, the funnel `serve`, `serve --http` and the facade all pass through, and asks one question per subsystem that a configuration can demand: the config demands it, and is the runtime object that serves it present? A configured-but-unreachable subsystem is never silent again. A tool gated behind approval with no gate service **refuses the boot** — a gateway that cannot hold a call is a gateway executing it unapproved, and starting anyway is failing open — and everything else logs at ERROR naming the subsystem and what asked for it. `startup_checks: {enforce: false}` downgrades the refusals to error logs; there is deliberately no switch that makes them silent

### Fixed

- **ci:** stop the post-publish smoke failing on PyPI index propagation. The `Published-artifact smoke (post-publish check)` job went red on the 2.0.1 release with `No matching distribution found for mcp-hangar==2.0.1` while `/pypi/mcp-hangar/2.0.1/json` was already answering 200 — the simple index pip reads lags a publish, and the retry budget (six flat 10-second waits, 53 seconds end to end) expired inside that window. A re-run minutes later passed with nothing changed, which is the actual damage: a gate that fires for reasons unrelated to the artifact teaches its reader to re-run without looking, and that is how a real packaging failure gets waved through. The budget is now ~3.5 minutes with exponential backoff, and when it is spent the script no longer guesses — it asks PyPI's JSON API, served from different infrastructure than the index, whether the version exists at all. The three outcomes now say three different things and only one is tolerated: published-but-unindexed warns (post-publish only, where the wheel is already immutable and a red buys nothing), a version PyPI never received fails, and a wheel that will not install fails immediately without burning the retry budget, because waiting cannot fix it ([#680](https://github.com/mcp-hangar/mcp-hangar/pull/680))
- **ci:** stop release-please proposing a backwards version bump after a hand-cut release. `release-please.yml` runs on push to `main`, so a hand-cut release — which merges its release commit first and pushes the tag second — leaves a window where no tag matches the version the manifest now carries. release-please falls back to an older baseline and derives a version from the whole commit range: twice in one day it proposed lowering `.release-please-manifest.json` (#670 from `2.0.0` to `1.6.1`, #677 from `2.0.1` to `1.6.1`) while rewriting `CHANGELOG.md` with 1.x history under a `v2.0.0...v1.6.1` compare link. Merging either would have corrupted the version state and the changelog together, and both were caught only by a human reading the diff. Two guards now compose. The run is skipped when the pushed commit is the hand-cut release of a version the manifest already carries — deliberately *not* when that commit came from release-please's own release branch, since that run is what creates the tag and skipping it would break bot-driven releases. And whatever version release-please computes, released or merely proposed in a pull request, the run fails loudly if it is lower than the manifest's; versions are compared as PEP 440, where `2.0.1` sorts below `1.6.1` as a string and `2.0.0rc4` sorts above `2.0.0` ([#681](https://github.com/mcp-hangar/mcp-hangar/pull/681))

## [2.0.1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.0.0...v2.0.1) (2026-07-31)

A single security fix. Behaviour changes in one direction only: a call whose world moved while its approval was pending is now refused where it previously executed — verified against a pre-fix build, which executed a tool that had been moved to `deny_list` during the hold.

Read the scope honestly: the approval gate is **not reachable on a stock `serve --http`** ([#678](https://github.com/mcp-hangar/mcp-hangar/issues/678)) — no config key gates a tool, the gate service is never constructed, and the REST routes read a field nothing sets. This fix is the guard that must be in place before that wiring lands, not a patch to a live exposure.

### Security

- **core:** re-establish an approval's validity at dispatch, not only at decision. The gate decided once and then blocked — 300 seconds by default, longer if configured — and every condition the decision rested on was evaluated *before* that pause and none of it after: effective policy, tool withdrawal, and the pinned tool digest were all checked at request time. Live config reload is a supported operation, so withdrawing a tool or tightening a policy while a decision was pending left the held call to dispatch on the superseded decision. Two fields made it worse by looking like they already handled it. `arguments_hash` was computed, persisted, emitted in events and shown to the approver, and compared against nothing — its own docstring says "for integrity checking" — while the request **mutator pipeline runs after the gate**, so a registered mutator could rewrite arguments a human had just agreed to with nothing to notice. `expires_at` was persisted and delivered and read by nothing: the only expiry that ran was the in-process `wait()` timeout, which dies with its waiter, so after a restart the row stayed `PENDING` past its window and `resolve()` still accepted it, minting an `APPROVED` record with a real `decided_by` for a call that never ran. (Measured against a pre-fix build: that resolve already returned 409 via the hold-release-failed branch, so the defect was never the status code — it was the false `APPROVED` record, which this changes along with the body.) `ApprovalGateService.revalidate()` now re-checks state, expiry and the argument hash; the executor calls it after the hold and additionally re-resolves the effective policy and re-runs the digest pin; `resolve()` refuses an expired approval with a new `EXPIRED` outcome mapped to **409**. Failing to re-verify refuses the call. `ApprovalRequest` also gains `requested_by` — the record named who decided and never who asked ([#674](https://github.com/mcp-hangar/mcp-hangar/pull/674))

## [2.0.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.0.0-rc.4...v2.0.0) (2026-07-31)

The 2.x line goes stable. Four changes decide whether this upgrade needs
planning; everything else is drop-in. The full detail is in the `2.0.0rc1`
through `2.0.0rc4` sections below — this is the short version, and
[the upgrade guide](https://mcp-hangar.io/docs/upgrade) is the operational one.

1. **Slack approval delivery moved out of core.** A config with
   `approvals.channel: slack` degrades to `noop` on 2.0.0 — approvals still
   queue and stay resolvable over REST, but nobody is notified until you run an
   adapter. Deliberate: refusing to boot over a *notification* channel turns a
   degraded path into an outage.
2. **Approval resolution is authorized.** `approval:resolve` was defined,
   granted to a role, and checked nowhere. A caller without it now gets `403`
   where it got `200`, and the client-supplied `x-principal-id` header no
   longer decides who a decision is attributed to.
3. **The gateway speaks MCP 2026-07-28** on the stable `mcp==2.0.0` SDK. Your
   upstreams do not have to: a connection that negotiates 2025-11-25 keeps
   working, and the modern `_meta` envelope is withheld on it.
4. **Tasks are served on the SEP-2663 wire.** `tasks/get` inlines the outcome;
   `tasks/result` and `tasks/list` answer `-32601`; the synchronous mid-flight
   consent prompt is gone, replaced by the governed `tasks/update` loop.

Security fixes land on 2.0.x only. The 1.6.x line is closed, and the approval
authorization fix is **not** backported.

### Added

- **core:** publish the REST surface as `api-routes.json`, generated from the routing table by `scripts/dump_api_routes.py`. Consumers build URLs against this API by hand across repositories, and there was no authoritative list to check them against: the operator called `/api/v1/*` for months after core moved to `/api/*`, leaving every remote `MCPServer` `Degraded` while working, with its own tests green throughout (they assert against a mock, and a mock answers whatever it is asked — operator#91). A unit test fails when the file drifts from the served routes, so a route change either updates it or breaks the build. Method and path only — response shapes are **not** covered, and a renamed field still breaks a consumer silently (ADR-011) ([#664](https://github.com/mcp-hangar/mcp-hangar/pull/664))

## [2.0.0rc4](https://github.com/mcp-hangar/mcp-hangar/compare/v2.0.0-rc.3...v2.0.0-rc.4) (2026-07-29)

### Added

- **core:** typed model for a pending approval, under the `io.mcp-hangar/approval` method namespace. Internal only — nothing is served yet. It serializes to a value that drops into a SEP-2663 `inputRequests` map with no transformation, so if modelcontextprotocol#2919 lands we plug in rather than rebuild. Deliberately carries **no** `requestedSchema`: a value with one looks like an elicitation to a client that does not know the method, and an elicitation is answerable by the caller — which is exactly the party an approval gate exists not to trust. The subject binds the call by `argumentsHash` rather than carrying the arguments, since it travels to whoever is deciding (A-2919 WS-5) ([#662](https://github.com/mcp-hangar/mcp-hangar/pull/662))

### Fixed

- **core:** recover from an upstream restart instead of wedging on it. Streamable HTTP answers a request carrying an unknown `Mcp-Session-Id` with 404, and the resolution is to open a new session — but the id was captured once and never cleared, 404 is not in `retry_status_codes`, and the caller saw an opaque `HTTP error: 404`. So after any upstream pod restart **every** call to that server failed, indefinitely, while `/health/ready` still reported the gateway healthy: nothing restarted it, nothing alerted, and recovery required restarting the *gateway* rather than the upstream. The client now drops the dead session and reports the condition distinguishably (`-32600` + `mcp_session_terminated`, matching what the SDK client reports), and the tool-invoke path re-handshakes once and retries. A **successful** renegotiation records no health failure — an ordinary restart is not evidence of an unhealthy upstream, and marking it so would pull a working pod out of its Service; a **failed** one does. Found on kind ([#651](https://github.com/mcp-hangar/mcp-hangar/pull/651))

### Security

- **core:** **BREAKING** core no longer knows any approval vendor. The `resolve` route dropped its `X-Slack-Signature` branch, `_handle_slack_callback` and `_get_slack_signing_secret` are gone, and `delivery/slack.py` left the tree. Both authentication branches were individually sound — HMAC-SHA256 over `v0:ts:body`, 300s freshness, `compare_digest` — but the shape was not: an unauthenticated caller chose which authentication mechanism ran. Delivery channels now resolve through the `mcp_hangar.approvals.delivery` entry-point group; core ships `dashboard` and `noop`. **Anyone with `approvals.channel: slack` configured must install an adapter**: it terminates the vendor webhook itself, verifies the signature, maps the vendor identity onto a Hangar principal, and calls `POST /approvals/{id}/resolve` with an ordinary token — which also retires `decided_by = f"slack:{id}"`, so provenance now names a Hangar principal rather than a vendor handle. A reference adapter ships in the docs. An unknown channel degrades to `noop` with a warning rather than failing startup: approvals then queue undelivered but stay resolvable over REST, whereas refusing to boot over a notification channel turns a degraded path into an outage (A-2919 WS-3/WS-4, ADR-016) ([#660](https://github.com/mcp-hangar/mcp-hangar/pull/660))
- **core:** tolerate clock drift when validating JWTs. `jwt.decode` was called without `leeway`, so PyJWT's default of 0 demanded that this host and the token issuer agree to the second on `exp`/`iat`/`nbf`. Skew is a property of the pair of hosts, so the failure is total rather than partial — a VM resuming from a snapshot, drifted NTP, or an IdP a few seconds ahead rejects **every** token at once, with valid credentials and a healthy IdP, and nothing in the token explains why. Both verifying paths (JWKS RS256/ES256 and static-secret HS256) now apply a tolerance, configurable per issuer as `clock_skew_leeway_seconds` and defaulting to 60 — the usual bound, and small enough not to meaningfully extend an expired token. Set it to 0 to restore exact agreement ([#630](https://github.com/mcp-hangar/mcp-hangar/pull/630))
- **core:** authorize approval resolution and attribute it to the authenticated caller. `approval:resolve` was defined in `auth/roles.py`, mapped from its string form and granted to a role — and checked nowhere, so any principal holding a valid token could decide any approval given its id. Resolution now goes through a command handler that authorizes before deciding; the check sits in the handler rather than the route so a second transport inherits it by construction. `decided_by` was worse than it looked: `_extract_principal` read `request.state.principal_id` and fell back to the client-supplied `x-principal-id` header, defaulting to `"unknown"` — but the auth middleware attaches `request.state.auth` and nothing ever set `principal_id`, so the header was the only path, including for authenticated requests, and that value landed in the provenance chain. Identity now comes from the platform's `get_principal_from_request`; with auth disabled the decision is attributed to the system principal, never a header and never a sentinel. Resolution remains possible on an auth-off gateway on purpose — refusing there would decide nothing, which is #600's shape of failing closed on the API and open on enforcement (A-2919 WS-0..WS-2) ([#656](https://github.com/mcp-hangar/mcp-hangar/pull/656))
- **ci:** scope every workflow's `GITHUB_TOKEN` to read-only by default. Nine workflows declared no top-level `permissions:`, so each inherited the repository default — write on Contents, Packages, Actions, Deployments, SecurityEvents and more. That was broadest exactly where it should be narrowest: in `release.yml` the `smoke-wheel` and `smoke-published` jobs **download and execute the freshly built artifact** (starting a gateway, spawning subprocess backends, driving a real MCP server) and `test` runs the suite — all with a near-omnipotent token, while the three jobs that genuinely publish had already scoped themselves down. Job-level blocks replace rather than merge with the default, so `publish-pypi`, `publish-docker`, `create-release`, `codeql` and `semgrep` keep exactly the scopes they declare ([#649](https://github.com/mcp-hangar/mcp-hangar/pull/649))

## [2.0.0rc3](https://github.com/mcp-hangar/mcp-hangar/compare/v2.0.0-rc.2...v2.0.0-rc.3) (2026-07-28)

The candidate that makes the published artifact match what the docs describe. It carries the whole SEP-2663 realignment — which `rc2` predates — and moves the SDK pin onto the **stable** `mcp==2.0.0`, released the same day.

### Added

- **core:** forward the caller's Tasks declaration upstream, per request. SEP-2663 leaves task augmentation to the upstream and gates it on the **caller** having declared `io.modelcontextprotocol/tasks` — and on the wire to an upstream, Hangar is that caller. Declaring nothing meant a spec-following upstream would never mint a task and the governed relay would sit idle having never been offered one. Forwarded only when the downstream caller declared it **and** the relay is actually wired: a connection-level claim would mint tasks for clients that never asked, and those same clients are then answered `-32021` on `tasks/get`, holding a handle they cannot use ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **ci:** watch for the conformance suite gaining auth support, which is what now blocks certifying the relay against the spec's own vectors. `@modelcontextprotocol/conformance@alpha` ships seven `tasks-*` extension scenarios — an external audit of exactly the SEP-2663 surface this repo serves — but they require `greet` / `slow_compute` in `tools/list`, and Hangar exposes backend tool names only in `front_door` topology, which projects per tenant and advertises **zero** tools without an identity (verified against a running gateway). `conformance server` has no `--header` / auth option, so it cannot present one. The two requirements are mutually exclusive, so this unblocks on the suite rather than on us ([#550](https://github.com/mcp-hangar/mcp-hangar/pull/550))

- **ci:** run the task-relay smoke drivers against a real gateway on every change to the relay or the example (`task-relay-smoke.yml`). `examples/` was covered by no workflow at all — CI built only `examples/provider_math` — which is why two defects shipped through a green unit suite and were found only by running the drivers by hand: the payload bridge (#638) and the capability advertisement (#639). Neither is reachable without a real client on a real connection, because the unit suite fakes the request context. Runs the upstream's own contract first, so a failure tells an upstream regression apart from a relay one ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **core:** vendor the SEP-2663 Tasks wire models (`mcp_hangar/tasks_wire.py`) instead of serving `mcp_types`' `Task*` types, which are the SEP-1686 generation 2026-07-28 removed from the core spec — flat `CreateTaskResult` with `resultType`, `ttlMs`/`pollIntervalMs`, `tasks/get` inlining its outcome, `tasks/result` and `tasks/list` gone, `tasks/update` present. Field names track [python-sdk#3005](https://github.com/modelcontextprotocol/python-sdk/pull/3005) so the models retire rather than fork when it merges; the one deliberate divergence is `GetTaskResult.inputRequests`, which #3005 drops on parse and Hangar's consent gate needs. No handler is rewired yet ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **tests:** pin what Hangar declares to an upstream at handshake — a hardcoded `"capabilities": {}`. Recorded because it decides whether the ADR-014 relay ever receives a task: SEP-2663 makes augmentation the upstream's decision and gates it on the caller declaring `io.modelcontextprotocol/tasks` (python-sdk#3005 answers a non-declaring client `-32021`), so against a spec-compliant upstream Hangar is a non-declaring client and no task is ever minted for it. Also records why per-downstream-client extension propagation is the wrong model — the upstream connection is one shared, cold-start connection that outlives any client ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

### Changed

- **deps:** pin the SDK to the stable `mcp==2.0.0` / `mcp-types==2.0.0` (from `2.0.0b2`). Verified rather than assumed: the full suite, the relay smoke harness against a live gateway, and the upstream contract all pass unchanged on the stable release. Note what did **not** arrive with it — SEP-2663 Tasks are still absent ([python-sdk#3005](https://github.com/modelcontextprotocol/python-sdk/pull/3005) remains open), and the `Task*` types are field-for-field identical across `b2`, `rc1` and `2.0.0`. A frozen region inside a moving beta could have been a snapshot mid-migration; one that ships unchanged in a major is a decision, so the vendored wire (ADR-015) stays. `pydantic-settings` leaves the dependency tree, which the SDK dropped at `rc1`

- **core:** drop a stale `type: ignore[attr-defined]` in `_sdk_compat`. `mcp.types` does not exist in the betas but the stable release carries it again, so mypy now resolves the import and the suppression became dead weight

- **core:** reactivate the governed task relay — `relay_tasks_enabled` defaults to **true** again on all three construction paths. It was turned off on 2026-07-28 because the surface advertised a wire it did not serve; ADR-015 Decision 5 set the condition for turning it back on as *the SEP-2663 shapes actually being served*, and they now are. Verified against a live gateway on a config that never mentions the flag: the extension is advertised on `server/discover` with the served method set, and the full relay lifecycle passes 22/22 — including the payload bridge, the refusal ladder, the `Mcp-Name` requirement and the governed `tasks/update` consent. The rollback path is unchanged and equally verified: `relay_tasks_enabled: false` advertises nothing and registers nothing ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **tests:** rewrite the task-relay smoke harness onto the SEP-2663 wire. The drivers negotiated `2025-11-25` and called `tasks/result` / `tasks/list`, so after the wire realignment every `tasks/*` answered `-32601` and the harness tested nothing — silently, because `examples/task_upstream` is not covered by any workflow. `drive_relay.py` now speaks through the SDK's `Client` with `mode="2026-07-28"` and typed requests carrying `name_param`, which is the only way to satisfy the mandatory per-request `Mcp-Name` header; `consent_hitl.py` is removed because Hangar no longer issues the `elicitation/create` prompt it drove; the example upstream now emits `inputRequests` so the governed `tasks/update` loop has something to key on. Two live defects were found by running it: the payload bridge and the capability advertisement ([#640](https://github.com/mcp-hangar/mcp-hangar/pull/640))

- **core:** serve the SEP-2663 Tasks wire. `tasks/get` now returns the flat vendored shape with `ttlMs`/`pollIntervalMs`, its outcome **inlined** (SEP-2663 folds the removed `tasks/result` round trip into the poll) and its `inputRequests` carried so the client can answer them; `tasks/cancel` and `tasks/update` return empty acknowledgements, because cancellation is cooperative and claiming a status the upstream never reported is the fabrication the SEP warns about. `tasks/result` and `tasks/list` are no longer registered, which is how they return `-32601`. `tasks/update` is now registered **unconditionally** — it was gated on an SDK probe that could never become true, so the handler was dead code (ADR-015). The advertised capability no longer claims `list` ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **core:** refuse `tasks/*` with the code SEP-2663 specifies: `-32601` on a 2025-11-25 connection (the methods do not exist there), `-32021` with a machine-readable `requiredCapabilities` for a modern client that never declared `io.modelcontextprotocol/tasks`, `-32020` for a missing or contradictory `Mcp-Name` header, `-32602` for an unknown or unowned task. The ladder is ordered version → routing header → capability, and each rung refuses before reaching the upstream ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **core:** enforce SEP-2663's mandatory `Mcp-Name: <taskId>` on `tasks/get|update|cancel` over HTTP. Neither the SDK nor Hangar's front-door middleware covers this — the SDK's `NAME_BEARING_METHODS` omits `tasks/*` and checks agreement rather than presence, and the middleware deliberately disengages on 2026-07-28 — so the handler gate is the only rung that runs. Skipped on stdio, where the SEP does not apply. `NAME_BEARING_TASK_METHODS` is exported for the operator's L7 selector (operator#53) ([#637](https://github.com/mcp-hangar/mcp-hangar/pull/637))

- **core:** correct two claims in the `tasks_wire` module docstring that a reader could disprove in seconds — `resultType` is not "absent entirely" from `mcp_types` (twelve result classes declare it, and `ResultType` accepts any string; the true and sufficient claim is that no `Task*` class does), and b2/rc1 are not "byte-identical" (the module was edited in that window — `SERVER_INFO_META_KEY` arrived, `DiscoverResult` lost `server_info`). The accurate version is the stronger argument: the Tasks surface is unchanged across both releases while the file around it is under active edit, so those types are a deliberately frozen region rather than a neglected one ([#636](https://github.com/mcp-hangar/mcp-hangar/pull/636))

### Removed

- **core:** the synchronous 2025-11-25 mid-flight consent flow, which resolved an `input_required` task by eliciting the downstream client inside `tasks/get`. It existed only for a wire Hangar no longer serves; on the SEP-2663 wire the client resolves its own input by driving `tasks/update`, which is governed and still fail-closed. What went with it: the decline / cancel / no-back-channel / elicit-error denial matrix and the concurrent-reprompt guard, all of which guarded an interactive prompt Hangar no longer issues. The digest re-verification that guarded `tasks/result` did **not** go with it — it moved to `tasks/get`, now the only path by which a payload reaches a caller ([#637](https://github.com/mcp-hangar/mcp-hangar/pull/637))

- **core:** `HAS_LIST_TASKS` / `HAS_TASKS_UPDATE` from `_sdk_compat`. Both probed `mcp_types`, which carries the frozen SEP-1686 generation, so neither could ever flip (ADR-015) ([#637](https://github.com/mcp-hangar/mcp-hangar/pull/637))

### Fixed

- **core:** stop stamping the 2026-07-28 `_meta` envelope on upstreams that negotiated a legacy protocol. From `mcp==2.0.0` the SDK enforces era separation: a connection whose `initialize` settled on 2025-11-25 rejects every later request carrying the modern envelope with `-32600`. Hangar stamped it unconditionally, so against any SDK-built legacy upstream `tools/list` failed, the cold start never completed, and the caller saw a **hang** rather than an error — the batch sat until its global timeout. The beta tolerated it; the stable release does not. The handshake now records the negotiated era and withholds the protocol keys on legacy connections, while still sending them to stateless SEP-2575 upstreams, which have no handshake and learn the protocol only from `_meta`. Caught by the published-artifact smoke (gate D) before the wheel shipped ([#550](https://github.com/mcp-hangar/mcp-hangar/pull/550))
- **core:** read the client's capabilities from the key the spec actually uses. `read_protocol_negotiation` looked for `io.modelcontextprotocol/capabilities`; the spec key is `io.modelcontextprotocol/clientCapabilities` — the SDK's inbound ladder requires it on every modern request, and the short spelling appears nowhere in `mcp_types`. So capabilities came back **empty for every well-formed request**, and nothing noticed because nothing consumed them (#291). The legacy spelling is still accepted; the spec key wins ([#644](https://github.com/mcp-hangar/mcp-hangar/pull/644))

- **core:** fetch a completed task's payload from an upstream that keeps it behind `tasks/result`. SEP-2663 inlines the result on `tasks/get`, so a modern upstream needs nothing extra — but an upstream on the older design answers `tasks/get` with a status only. Serving `tasks/result` downstream was correctly removed; no longer *calling* it upstream went with it by accident, which made the payload of every task relayed from such a server unreachable: the client polled to `completed` and got `result: null` forever. The fetch is best-effort (a modern upstream answers `-32601` there, which is not a reason to fail a good poll) and runs only after the pinned-digest re-verification, so a drifted tool is never asked for output ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **core:** advertise the Tasks surface under `capabilities.extensions`, where SEP-2663 puts it, instead of `capabilities.tasks`. `v2026_07_28.ServerCapabilities` has no `tasks` field — SEP-2663 moved Tasks out of the core capability set — so the SDK's per-version serialization sieve silently dropped it from a modern `server/discover`. The result was exactly inverted: the field survived on the legacy handshake, where Hangar refuses `tasks/*` with `-32601`, and vanished on the modern wire, where Hangar actually serves them. A spec-following 2026-07-28 client could never discover the surface. The advertised settings now name the served method set itself, so the advertisement and the registration cannot drift ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **ci:** retarget the gate E ecosystem watch, which was reporting a false all-clear. It asked whether `@modelcontextprotocol/sdk` had published a 2.x — but the TypeScript SDK v2 shipped under **new package names** (`@modelcontextprotocol/core` / `client` / `server`, all 2.0.0) while `sdk` continues on the v1 line, so the check answered "still on the v1 line" about a superseded package and stayed silent about the event it existed to catch. The Inspector check had the mirror problem: it read a dependency key the Inspector no longer has. The scenario-drift check only compared against `latest`, missing that the 2026-07-28 server vectors and the whole `tasks-*` extension family landed on the `alpha` tag while `latest` sat at 0.1.16 ([#550](https://github.com/mcp-hangar/mcp-hangar/pull/550))

## [2.0.0rc2](https://github.com/mcp-hangar/mcp-hangar/compare/v2.0.0-rc.1...v2.0.0-rc.2) (2026-07-28)

A single-fix candidate cut directly on top of `2.0.0rc1`, which advertises a task capability it cannot serve to a modern client.

### Added

- **core:** run the official MCP conformance suite against a real `serve --http` gateway in CI, with a classified baseline of known failures (gate E of #550). The 2026-07-28 server vectors do not exist upstream yet, so this certifies the back-compat generation; a weekly advisory job watches all three things gate E is blocked on: the modern scenarios, an SDK 2.x on npm, and the Inspector moving off SDK v1 ([#550](https://github.com/mcp-hangar/mcp-hangar/pull/550))

- **core:** smoke the *published artifact* rather than the repo tree before and after every release (gate D of #550) — a clean venv installs the wheel the way a user would, then a real `hangar_call` is driven through the gateway to a cold backend; `publish-pypi` now depends on the pre-publish run, so a wheel broken only by its packaging can still be stopped ([#550](https://github.com/mcp-hangar/mcp-hangar/pull/550))

### Fixed

- **core:** stop advertising the `tasks` capability by default — `relay_tasks_enabled` now defaults to **False** on all three construction paths (`ServerConfig`, the builder, the HTTP-serve bootstrap), which previously disagreed with each other (True / False / True). `2.0.0rc1` shipped the relay on by default, so a client negotiating 2026-07-28 was told the server speaks `tasks` and then served the SEP-1686 shapes `mcp_types` still carries — nested `CreateTaskResult{task}`, `ttl`, `pollInterval`, a `tasks/result` method that SEP-2663 removes, and no `resultType`. The client cannot detect the mismatch before it gets a reply it cannot parse. Those types are a fossil and never evolve in place — SEP-2663 lands as a separate extension with its own models (python-sdk#3005) — so the surface is opt-in until Hangar serves that wire ([#322](https://github.com/mcp-hangar/mcp-hangar/pull/322))

- **core:** cap `httpx` below 1.0 — httpx 1.0 drops `httpx.AsyncClient`, which the proxy path uses throughout, so the documented `pip install --pre mcp-hangar` resolved `httpx==1.0.dev3` and the gateway could not start at all. Found by the new published-artifact smoke on its first run against a real release ([#618](https://github.com/mcp-hangar/mcp-hangar/pull/618))

## [2.0.0rc1](https://github.com/mcp-hangar/mcp-hangar/compare/v2.0.0-alpha.2...v2.0.0-rc.1) (2026-07-27)

First release candidate for 2.0.0 — the SDK v2 / MCP 2026-07-28 line.

This section covers **everything on the 2.x line since it diverged from 1.x**, alphas included: `v2.0.0-alpha.1` and `v2.0.0-alpha.2` were published without changelog sections, and the entries below had accumulated in three separate `## [Unreleased]` blocks left behind by successive reconcile merges from `main`. They are consolidated here rather than split retroactively, since no reader can install an alpha expecting a subset.

### Added

- **cli:** `mcp-hangar auth bootstrap-admin --config PATH --principal PRINCIPAL` grants the one-time initial global admin using the server's own durable auth backend (reuses `bootstrap_auth()`, never an in-memory store). Fails closed when auth is disabled, anonymous access is allowed, or the storage driver is non-durable (`memory`/`event_sourcing`); a second run is refused without mutating storage. No credential is printed -- the grant is a global admin role for an existing external principal ([#451](https://github.com/mcp-hangar/mcp-hangar/pull/451))
- **core:** read the client `protocolVersion`/capabilities from inbound `params._meta` per request (stateless negotiation, no session handshake), exposed via request context (#291) ([#375](https://github.com/mcp-hangar/mcp-hangar/pull/375))
- **security:** projected `tools/list` responses advertise a tenant-scoped `cacheScope` (SEP-2549) so downstream caches cannot serve one tenant's list to another; fail-closed to the narrowest scope when the tenant is unknown (#292) ([#372](https://github.com/mcp-hangar/mcp-hangar/pull/372))
- **core:** add the MCP policy DSL parser/validator (v1 grammar; hooks tcp_connect/sk_alloc/execve/openat) per ADR-006, backend-agnostic and compiler-ready (#329) ([#358](https://github.com/mcp-hangar/mcp-hangar/pull/358))
- **core:** enforcement events (`CapabilityViolationDetected`, `EgressBlocked`) carry optional process-attribution fields (pid/container/pod/node) for the Tetragon backend and forensic chain (#331) ([#363](https://github.com/mcp-hangar/mcp-hangar/pull/363))
- **core:** opt-in interceptor configuration -- register built-in validators (e.g. `payload_size`) via an `interceptors:` config section; off by default, no behavior change (#314) ([#364](https://github.com/mcp-hangar/mcp-hangar/pull/364))
- **core:** propagate W3C `baggage` (SEP-414) with a fail-safe cross-tenant scrub that drops untrusted/cross-tenant baggage on outbound (#294) ([#365](https://github.com/mcp-hangar/mcp-hangar/pull/365))
- **core:** GovernedTaskStore binds each MCP task to its owning tenant/principal and fail-closed-authorizes tasks/* access, wiring the TaskOwnershipRegistry into the (experimental) task lifecycle (#319) ([#366](https://github.com/mcp-hangar/mcp-hangar/pull/366))
- **core:** digest pinning now spans the task lifecycle -- a task inherits its tool's pinned digest and the result is re-verified against the tool's current digest, failing closed on drift (#320) ([#367](https://github.com/mcp-hangar/mcp-hangar/pull/367))
- **core:** advertise Hangar governance (interceptors, digest pinning) as SEP-2133 extensions under `capabilities.experimental` (reverse-DNS, opt-in, off by default) (#316) ([#370](https://github.com/mcp-hangar/mcp-hangar/pull/370))
- **core:** command-bus rate limit (rps/burst) is configurable via a `rate_limit:` `config.yaml` section (config > env > default); previously env-only (#395) ([#396](https://github.com/mcp-hangar/mcp-hangar/pull/396))
- **core:** add a fail-closed `TaskOwnershipRegistry` binding `taskId` to its owning tenant/principal, to authorize `tasks/*` access (#319) ([#352](https://github.com/mcp-hangar/mcp-hangar/pull/352))
- **core:** add a fail-closed `TaskDigestGuard` that pins a tool digest per `taskId` and re-verifies it on task completion, extending digest pinning across the task lifecycle (#320) ([#356](https://github.com/mcp-hangar/mcp-hangar/pull/356))
- **core:** interceptor Validator framework — `IValidator` contract + fail-closed `ValidatorPipeline` + a reference `PayloadSizeValidator`; validators default to `failOpen=false` per PR #2624 (#314) ([#351](https://github.com/mcp-hangar/mcp-hangar/pull/351))
- **core:** add a fail-closed `TaskConsentGate` that gates mid-flight task input (`input_required` / `tasks/update`), rejecting answers with no pending consent (#322) ([#357](https://github.com/mcp-hangar/mcp-hangar/pull/357))
- **security:** atomically bootstrap the first API-key administrator in durable SQLite and PostgreSQL auth stores (#450) ([#456](https://github.com/mcp-hangar/mcp-hangar/pull/456))
- **core:** reconcile the interceptor surface with MCP PR #2624 — add `interceptor/invoke`, hook objects carrying `events` + `phase` (`request`/`response`), and phase-aware hook delivery on the request/response path. Opt-in and behind capability negotiation (header `MCP-Interceptor-Ext: io.modelcontextprotocol/interceptors` or `?ext=io.modelcontextprotocol/interceptors`); the default `interceptors/list` shape is unchanged. Pinned to PR #2624 head `8029c78` (OPEN — wire format may still move) (#317, #401) ([#400](https://github.com/mcp-hangar/mcp-hangar/pull/400))
- **core:** emit task-lifecycle audit events (`TaskCreated`, `TaskInputRequired`, `TaskCompleted`, `TaskFailed`, `TaskCancelled`) carrying `tenant_id` + `task_id` + `correlation_id`; the audit trail records all five and is reconstructable per `task_id` (#321) ([#399](https://github.com/mcp-hangar/mcp-hangar/pull/399))
- **core:** configurable command-bus rate limit via `config.yaml` `rate_limit.rps` / `rate_limit.burst`; config values take precedence over the `MCP_RATE_LIMIT_RPS` / `MCP_RATE_LIMIT_BURST` env vars, which remain as a fallback (#395) ([#398](https://github.com/mcp-hangar/mcp-hangar/pull/398))
- **tests:** schema validation for `interceptors/list` response against local JSON Schema derived from SEP-1763 (pinned @ `99bc7c9`) (#185, #401) ([#191](https://github.com/mcp-hangar/mcp-hangar/pull/191))
- **core:** add a SEP-2575 (Stateless MCP) `server/discover` entry point backed by the existing per-tenant projection read-model (#237). It returns the tenant-scoped tool surface — identical to the tenant's `tools/list` projection — alongside `supportedVersions`, `capabilities`, and `serverInfo`, so a stateless client can discover exactly the tools its tenant may call in one call. Tenant scoping and isolation are inherited from the projection (tenant A never sees tenant B's tools) (#290) ([#407](https://github.com/mcp-hangar/mcp-hangar/pull/407))
- **observability:** add `mcp_hangar_otlp_export_failures_total` counter, incremented via a `SpanExporter` decorator when an OTLP span-export batch fails (collector unreachable/export error), so otherwise-silent background export failures and dropped spans are observable on `/metrics`; document the `MCP_TRACING_ENABLED=false` off-switch for running locally without a collector (#402) ([#419](https://github.com/mcp-hangar/mcp-hangar/pull/419))
- **observability:** add `mcp_hangar_otlp_export_failures_total` counter, incremented via a `SpanExporter` decorator when an OTLP span-export batch fails (collector unreachable/export error), so otherwise-silent background export failures and dropped spans are observable on `/metrics`; document the `MCP_TRACING_ENABLED=false` off-switch for running locally without a collector (#418) ([#419](https://github.com/mcp-hangar/mcp-hangar/pull/419))
- **core:** add the L7 egress policy engine (`domain.policies.egress_l7`): deterministic tool-call matching (glob allow / deny / require-approval with a policy default action) and argument scanning (named secret-pattern groups reusing the output redactor's value-regexes, plus a payload-size limit). Pure and deterministic — no ML. This is the core-side half of `MCPEgressPolicy` ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53)) ([#526](https://github.com/mcp-hangar/mcp-hangar/pull/526))
- **core:** receive the compiled L7 egress policy from the operator over the REST API — `POST/PUT /api/mcp_servers/{id}/l7_policy` (set/replace) and `DELETE` (clear), guarded by the `mcp_servers:write` permission. Adds `L7Policy.from_dict` (parses the operator's camelCase wire form) and a `SetL7PolicyCommand`/handler that calls `McpServer.set_l7_policy`, closing the operator→core transport so an `MCPEgressPolicy` drives L7 enforcement end to end ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53)) ([#528](https://github.com/mcp-hangar/mcp-hangar/pull/528))
- **core:** enforce the L7 egress policy at the tool-invocation chokepoint. `McpServer` carries an optional L7 policy; `invoke_tool` evaluates every call against it before waking the server or touching the upstream — a denied call raises `EgressPolicyDeniedError`, an approval-gated one raises `EgressPolicyApprovalRequiredError`, and neither reaches the wire. No policy attached means no enforcement (unchanged behavior). Populating the policy from the operator's compiled `MCPEgressPolicy` is the remaining transport step ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53)) ([#527](https://github.com/mcp-hangar/mcp-hangar/pull/527))
- **observability:** trace the upstream call boundary. Outgoing MCP RPCs (`tools/call`, `tools/list`, `initialize`) are now `SpanKind.CLIENT` spans named per OTel GenAI/MCP semconv (`execute_tool {tool}`, with `gen_ai.tool.name` / `gen_ai.operation.name` / `mcp.method.name`) so an upstream's server span parents correctly to the gateway. The stdio transport now propagates W3C trace context into the MCP `_meta` field, mirroring the HTTP header injection, so distributed tracing survives stdio upstreams too. `init_tracing` now honors `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` (`always_on`/`always_off`/`traceidratio`/`parentbased_*`), which the hand-built `TracerProvider` previously ignored despite the documented contract ([#537](https://github.com/mcp-hangar/mcp-hangar/pull/537))
- **observability:** add two telemetry-health Prometheus alerts (`monitoring/prometheus/alerts.yaml`): `MCPHangarTelemetryExportFailing` (fires on `rate(mcp_hangar_otlp_export_failures_total[5m]) > 0` — a silent OTLP export failure means traces are being dropped with no app-level error) and `MCPHangarDiscoveryValidationFailing` (discovered servers being rejected on validation). Closes a coverage gap for two emitted-but-unalerted signals; validated with `promtool check rules` ([#541](https://github.com/mcp-hangar/mcp-hangar/pull/541))

### Changed

- **core:** the stateless front door routes on `Mcp-Method`/`Mcp-Name` headers instead of session affinity (SEP-2243/SEP-2567); per-tenant canary routing and audit correlation are unchanged (#336) ([#377](https://github.com/mcp-hangar/mcp-hangar/pull/377))
- **core:** reject upstream MCP task handles with a clear error instead of passing through an untracked, unusable handle (relay-only; task results are not yet governed) (#302) ([#368](https://github.com/mcp-hangar/mcp-hangar/pull/368))
- **core:** the transport `Mcp-Session-Id` handling is deprecated and guarded per SEP-2567 (stateless); it is only echoed for legacy session-based upstreams that established a session, and a `stateless_upstream` flag disables it outright. The audit `session_id` correlation is unchanged (#337) ([#379](https://github.com/mcp-hangar/mcp-hangar/pull/379))
- **core:** clarify that `mode: docker`/`container` requires a podman or docker CLI on the host; the no-runtime start error and `config.yaml.example` now state that container mode is unsupported inside the stock Hangar container image and advise running in host mode or using a subprocess provider ([#429](https://github.com/mcp-hangar/mcp-hangar/pull/429))
- **core:** the interceptor ValidatorPipeline now runs on the tool-call path; registered validators deny fail-closed before invoke (empty/no-op by default) (#314) ([#359](https://github.com/mcp-hangar/mcp-hangar/pull/359))
- **core:** the interceptor MutatorPipeline now runs on the tool-call path (request/response payload transform; empty/no-op by default) (#314) ([#361](https://github.com/mcp-hangar/mcp-hangar/pull/361))
- **core:** interceptor IDs use reverse-DNS extension identifiers (`io.mcp-hangar.validator`/`io.mcp-hangar.mutator`) per SEP-2133 (#315) ([#346](https://github.com/mcp-hangar/mcp-hangar/pull/346))
- **core:** inbound trace context is read from the request's `params._meta` (SEP-414), falling back to the legacy `metadata` field, so agent traces link end-to-end (#294) ([#344](https://github.com/mcp-hangar/mcp-hangar/pull/344))
- **core:** outbound HTTP requests carry W3C trace context (`traceparent`/`tracestate`) in `params._meta` per SEP-414, in addition to HTTP headers (#294) ([#343](https://github.com/mcp-hangar/mcp-hangar/pull/343))
- **core:** outbound requests to upstream MCP servers carry the protocol version and client info in per-request `_meta`, so stateless upstreams (SEP-2575, no initialize handshake) still receive protocol context (#291) ([#342](https://github.com/mcp-hangar/mcp-hangar/pull/342))
- **core:** outbound handshake to upstream MCP servers targets MCP protocol revision `2026-07-28` and tolerates stateless upstreams (servers without an `initialize` handler) instead of failing startup (#341) ([#342](https://github.com/mcp-hangar/mcp-hangar/pull/342))
- **core:** document the static `tools:` list as a pre-start visibility projection (the provider's dynamic `tools/list` is authoritative and replaces it at start) and log a warning naming any statically pre-configured tool the provider does not return (#415) ([#421](https://github.com/mcp-hangar/mcp-hangar/pull/421))
- **core:** **BREAKING** relicense from BSL 1.1 dual-license to MIT; all enterprise features are now freely available (#198)
- **core:** remove `LicenseTier` enum, `LicenseValidation`, and license-key gating from bootstrap; `load_enterprise_modules` loads unconditionally (#196)
- **core:** `HANGAR_LICENSE_KEY` env var is deprecated and emits `DeprecationWarning` when set (#196)
- **core:** `EnterpriseComponents` no longer carries a `license_tier` field; `ApplicationContext.license_tier` removed (#196)
- **core:** reject tool entries with missing, empty, or non-string `name` field in `compute_tool_digest` (#172) ([#186](https://github.com/mcp-hangar/mcp-hangar/pull/186))
- Public documentation migrated to dedicated [docs repository](https://github.com/mcp-hangar/docs). Internal docs remain in `docs/internal/`.
- **observability:** align tool-invocation spans to OTel GenAI/MCP semantic conventions. The application-layer span is renamed `tool.invoke.{tool}` → `execute_tool {tool}` (matching the transport CLIENT span from the previous change), and the tool-name / token attributes move to semconv: `mcp.tool.name` → `gen_ai.tool.name`, `mcp.cost.input_tokens` / `mcp.cost.output_tokens` → `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`, with `gen_ai.operation.name` and `mcp.method.name` now also set. **Breaking for consumers that query the old span/OTLP-audit attribute names.** The Hangar-specific governance namespaces (`mcp.enforcement.*`, `mcp.risk.*`, `mcp.audit.*`, `mcp.cost.cents`/`model`/`currency`, `mcp.session.id`, …) are unchanged — they have no semconv equivalent. Also restores OTLP audit-log export, which a botched `Provider`→`McpServer` rename had silently disabled (the `LoggerMcpServer` import always failed, pinning `OTEL_LOGS_AVAILABLE` to false). Found continuing the observability audit ([#539](https://github.com/mcp-hangar/mcp-hangar/pull/539))

### Removed

- **core:** delete `enterprise/auth/license.py` (HMAC license-key validator) (#196)
- **core:** delete `src/mcp_hangar/domain/value_objects/license.py` (`LicenseTier` enum) (#196)
- **core:** delete `enterprise/LICENSE.BSL` and `CLA.md` (#194, #197)
- **core:** remove CLA references from contributing guides (#197)
- **core:** strip BSL prose from `CONTRIBUTING.md`, `ROADMAP.md`, enterprise docstrings, and `PRODUCT_ARCHITECTURE.md` decision log (#195)
- **observability:** remove unused `Metrics.COLD_STARTS_TOTAL`, `Metrics.EGRESS_BLOCKED_TOTAL`, and `Metrics.PROVIDERS_QUARANTINED` constants — they had no backing metric in `metrics.py` ([#270](https://github.com/mcp-hangar/mcp-hangar/pull/270))
- **core:** retire the Hangar Cloud connector (`src/mcp_hangar/cloud/`), the `POST /agent/policy` endpoint, the `--cloud-key`/`--cloud-url` CLI flags, and the `agent` RBAC role, as the hangar-agent / Hangar Cloud product tier is retired. `PolicyPushRejected` is intentionally kept (deprecated, producer-less) so already-persisted events still replay; `policy:write` remains and is granted via the `admin` role. ([#490](https://github.com/mcp-hangar/mcp-hangar/pull/490))

### Fixed

- **security:** `JWTAuthenticator` read the `Authorization` header case-sensitively (`get("Authorization")`), but the HTTP auth middleware lowercases header names (ASGI headers already are), so `supports()` never matched a bearer request -- every valid OIDC/JWT token over `serve --http` was rejected as `auth_method: none` and OIDC bearer auth was non-functional on the HTTP surface. Now reads the header case-insensitively, matching `ApiKeyAuthenticator` ([#471](https://github.com/mcp-hangar/mcp-hangar/pull/471))
- **security:** the SQLite role store seeded built-in roles with `INSERT OR REPLACE`, which deletes the conflicting row; because `role_assignments.role_name` has `ON DELETE CASCADE`, re-initializing the store (every process start / `bootstrap_auth`) silently cascade-wiped every assignment to a built-in role -- dropping the bootstrapped admin on the next restart. The seed (and `add_role`) now upsert in place via `ON CONFLICT(name) DO UPDATE`, matching the PostgreSQL store, so assignments survive ([#451](https://github.com/mcp-hangar/mcp-hangar/pull/451))
- **core:** `EventStoreConfigurationError` now subclasses the domain `ConfigurationError` (was `RuntimeError`), so the event-store fail-fast surfaces as a configuration error at the config boundary; realigned the enterprise-boundary tests that asserted the pre-`#428` exception type/message, unbreaking `CI - Core` on `main` ([#459](https://github.com/mcp-hangar/mcp-hangar/pull/459))
- **core:** `config.yaml.example` used a `providers:` server section, but the loader requires `mcp_servers:` and raises `Invalid configuration: missing 'mcp_servers' section` -- copying the example verbatim failed to start. Renamed to `mcp_servers:` (and the `mcp_servers.*.max_concurrency` doc path) ([#458](https://github.com/mcp-hangar/mcp-hangar/pull/458))
- **auth:** the OIDC/JWT authenticator now matches the `Authorization` header case-insensitively, so a real Bearer token from the HTTP transport (which normalises header keys to lowercase) is authenticated instead of silently falling through to "no authenticator matched" (#311) ([#383](https://github.com/mcp-hangar/mcp-hangar/pull/383))
- **core:** `ProtocolNegotiation.capabilities` uses `default_factory` instead of a bare `mappingproxy` default, which Python 3.11's dataclass rejects as a mutable default -- this was breaking test collection on 3.11 across the whole suite (#291) ([#378](https://github.com/mcp-hangar/mcp-hangar/pull/378))
- **security:** fail-closed `ui://` (MCP Apps) resource guard -- per-tenant allowlist + restrictive CSP + mandatory consent gate; `ui://` denied by default (#328) ([#381](https://github.com/mcp-hangar/mcp-hangar/pull/381))
- **core:** inbound trace context and protocol-version/capability negotiation now reach the executor over streamable-HTTP (`hangar_call` threads the request context in), instead of silently defaulting; identity bridging (#387) unchanged (#294) ([#397](https://github.com/mcp-hangar/mcp-hangar/pull/397))
- **core:** fail fast when the SQLite event store cannot be initialized (path not writable / backend unavailable) instead of silently degrading to a non-durable in-memory store and losing the audit/event-sourcing trail; opt into the non-durable fallback with `event_store.driver: memory` or `event_store.allow_memory_fallback: true`. Also adds an `event_store_durability` readiness check so `/health/ready` returns 503 when the store degraded to in-memory while a durable driver was configured ([#428](https://github.com/mcp-hangar/mcp-hangar/pull/428))
- **core:** treat a backend MCP tool result with `isError: true` as a tool failure instead of a success, so per-call results, batch `succeeded`/`failed` counts, health, and `ToolInvocationFailed` events reflect reality ([#423](https://github.com/mcp-hangar/mcp-hangar/pull/423))
- **core:** run discovery on a dedicated lifecycle event loop so blocking discovery sources cannot block HTTP serving and shutdown awaits cleanup on the same loop (#436) ([#446](https://github.com/mcp-hangar/mcp-hangar/pull/446))
- **core:** expose bootstrapped discovery sources and pending providers through the canonical `/api/discovery` REST endpoint prefix (#434) ([#442](https://github.com/mcp-hangar/mcp-hangar/pull/442))
- **core:** reload configured mcp_servers through their supported shutdown lifecycle API and fail the reload when the old runtime cannot be stopped (#433) ([#441](https://github.com/mcp-hangar/mcp-hangar/pull/441))
- **core:** allow every concurrent cold-start waiter to invoke after the shared startup succeeds instead of timing out while the provider reaches READY (#435) ([#440](https://github.com/mcp-hangar/mcp-hangar/pull/440))
- **core:** fail startup when a configured SQLite event store is unavailable instead of silently falling back to volatile memory storage (#428) ([#438](https://github.com/mcp-hangar/mcp-hangar/pull/438))
- **core:** re-pin the interceptor JSON schema (`5bd7ab4` → `99bc7c9`) and reconcile the capability-negotiation key with the SEP-2133 extensions format adopted upstream in experimental-ext-interceptors #25; the `interceptor/invoke` + negotiated `interceptors/list` gate now keys on `io.modelcontextprotocol/interceptors` (was `sep-2624`), so clients negotiating per current upstream reach the gate. Off-by-default posture preserved (#401) ([#405](https://github.com/mcp-hangar/mcp-hangar/pull/405))
- **core:** group circuit breaker no longer blocks member selection while a healthy member remains in rotation; the group CB now only vetoes selection when no member is in rotation (the group genuinely down), so an evicted primary failing over to a healthy backup is served instead of returning "No available member" (#425) ([#426](https://github.com/mcp-hangar/mcp-hangar/pull/426))
- **cli:** accept `--config`/`-c` on the `serve` subcommand so `mcp-hangar serve --config X` no longer fails with "No such option"; emit the unambiguous global-first arg order (`["--config", path, "serve"]`) in the generated Claude Desktop config so `mcp-hangar init` produces an entry that actually starts (#417) ([#420](https://github.com/mcp-hangar/mcp-hangar/pull/420))
- **metrics:** wire `mcp_hangar_connections_active` (set 1 when a server's client connects, 0 on close/shutdown) so the provider-details "Active Connections" panel has data, and **remove** the never-emitted `mcp_hangar_connections_total` / `mcp_hangar_connection_duration_seconds` — no dashboard or alert referenced them and they duplicated the server-lifecycle signals. Found by the observability audit ([#536](https://github.com/mcp-hangar/mcp-hangar/pull/536))
- **metrics:** wire the transport message metrics — `mcp_hangar_messages_sent` (by `method`), `mcp_hangar_messages_received` (by `type`: response/notification/error), and the `mcp_hangar_message_size_bytes` payload-size histogram (by `direction`) — at the stdio and HTTP transport boundaries, labeled per upstream server. These were defined but never emitted, so the protocol-level and payload-size panels stayed empty. **Removed** three never-emitted metrics that have nothing to populate them: `mcp_hangar_http_connection_pool_size` (httpx pool internals aren't exposed) and `mcp_hangar_http_sse_streams_active` / `mcp_hangar_http_sse_events` (the streaming-SSE reader path is unused — SSE responses are batch-parsed). Repurposed the dead "Active SSE Streams" dashboard panel to a messages-sent rate. Found by the observability audit ([#540](https://github.com/mcp-hangar/mcp-hangar/pull/540))
- **metrics:** emit the cost-attribution metrics (`mcp_hangar_cost_cents_total`, `mcp_hangar_cost_attributions_total`). The cost handler computed per-invocation cost via the `ICostAttributor` port and published a report event, but never fed the Prometheus metrics its docstring promised — so the governance dashboard's cost panels stayed empty even with a real attributor configured. Now wired (a no-op under the default `NullCostAttributor`). Found by the observability audit ([#535](https://github.com/mcp-hangar/mcp-hangar/pull/535))
- **metrics:** consolidate discovery metrics onto the single scraped registry. Discovery registrations, errors, validation failures, and validation durations were recorded only to a second `prometheus_client` registry that the `/metrics` endpoint never serialized — so they were silently dropped, and cycle/quarantine/deregistration were double-recorded. Removed the dead secondary system (`application/discovery/discovery_metrics.py`), added the two missing metrics (`mcp_hangar_discovery_validation_failures_total`, `mcp_hangar_discovery_validation_duration_seconds`) to the primary registry, and rewired the orchestrator through it. Found by the observability audit ([#534](https://github.com/mcp-hangar/mcp-hangar/pull/534))
- **observability:** stop logging expected stdio-server shutdowns as errors. When Hangar closes a subprocess server (idle-TTL expiry / explicit stop), `close()` sets the client closed before terminating, so the reader thread's `stdio_client_process_exited` (+ any drained stderr) was logged at ERROR on every graceful shutdown — inflating the error stream and any log-based alerting. These are now logged at `info` with `expected=true` when we initiated the exit; an unsolicited process death is still an ERROR. Found reviewing live logs in Loki ([#542](https://github.com/mcp-hangar/mcp-hangar/pull/542))
- **observability:** mark a failed tool call's span as ERROR. The batch executor handles failures as data (`CallResult.success=False`), so the `batch.call.{tool}` span never saw an exception and stayed UNSET — a failing tool call looked like a successful trace and couldn't be filtered as an error in Jaeger/Tempo. It now sets ERROR status (with the failure message) when the call fails. Added a NoOp-safe `mark_span_error` helper. Found reviewing the error path on the live stack ([#544](https://github.com/mcp-hangar/mcp-hangar/pull/544))
- **observability:** correlate logs with traces — every log record emitted inside an OpenTelemetry span now carries `trace_id`/`span_id`, so you can pivot from a log line to its trace. A no-op when tracing is off or there is no active span, and it never lets a tracing error break a log call. Found by the observability audit ([#533](https://github.com/mcp-hangar/mcp-hangar/pull/533))
- **metrics:** the tool-call latency histogram (`mcp_hangar_tool_call_duration_seconds`) no longer records a 0-second observation for every failed call — failures carried no real duration and poisoned the p50/p95/p99 percentiles. Duration is observed only for successful calls; failures are still counted via `mcp_hangar_tool_call_errors_total`. Found by the observability audit ([#532](https://github.com/mcp-hangar/mcp-hangar/pull/532))
- **metrics:** drop the unbounded `stream_id` label from `mcp_hangar_events_compacted_total` — stream IDs are per-stream identifiers and were a cardinality bomb. Compaction is now a fleet-wide counter. Found by the observability audit ([#532](https://github.com/mcp-hangar/mcp-hangar/pull/532))
- **core:** discovered `http`/`sse` containers now prefer the published host-port binding over the internal bridge-network IP, so they are reachable from the documented host-mode deployment ([#481](https://github.com/mcp-hangar/mcp-hangar/pull/481))
- **core:** allow a discovery-only `config.yaml` (`discovery.enabled: true`, no top-level `mcp_servers`) to load instead of raising ([#483](https://github.com/mcp-hangar/mcp-hangar/pull/483))
- **core:** log the transient "container has no IP" discovery skip at debug instead of warning ([#484](https://github.com/mcp-hangar/mcp-hangar/pull/484))
- **core:** serve the SEP-2575 `server/discover` entry point over `serve --http` — it 404'd on the shipped CLI because the wiring lived only in the never-called `MCPServerFactory` ([#560](https://github.com/mcp-hangar/mcp-hangar/pull/560))
- **core:** report one `serverInfo` identity to clients — `initialize` announced `mcp-registry` at the mcp SDK's version while `server/discover` announced `mcp-hangar` at Hangar's ([#560](https://github.com/mcp-hangar/mcp-hangar/pull/560))
- **core:** keep the SEP-2243 front-door wrap off 2026-07-28 requests — buffering and replaying a modern-era body makes the SDK read a disconnect and cancel, answering 500 ([#560](https://github.com/mcp-hangar/mcp-hangar/pull/560))
- **core:** let a task owner reach their own task when auth is enabled — the identity bridge read only the SDK v1 `ctx.request_context.request`, so on v2 every `tasks/*` call over `serve --http` was unattributed and the governed relay was dead in the deployment mode that matters ([#598](https://github.com/mcp-hangar/mcp-hangar/pull/598))
- **core:** stop requiring a warm backend for `/health/ready` — lazy start plus `idle_ttl_s` makes "every backend cold" the normal idle state, so readiness flipped to 503, Kubernetes removed the pod from its Service, and no call could arrive to warm a backend again ([#599](https://github.com/mcp-hangar/mcp-hangar/pull/599))
- **core:** allow the REST API when auth is disabled — the permission guard tested only for an authz middleware, which `NullAuthComponents` still ships, so every REST call answered 401 with no credential able to open it and the operator could not deliver L7 egress policy to an auth-off gateway ([#600](https://github.com/mcp-hangar/mcp-hangar/pull/600))
- **core:** enforce a tenant's digest pin on the first call after gateway boot — the tool catalogue is published by the backend's start, which happens after the pin gate ran, so the first call to a pinned tool skipped the check entirely ([#601](https://github.com/mcp-hangar/mcp-hangar/pull/601))
- **core:** report the server's real capabilities from `server/discover` — it returned a hardcoded set, so a stateless client (which has no `initialize` to learn from) was told Tasks, prompts and resources did not exist ([#605](https://github.com/mcp-hangar/mcp-hangar/pull/605))
- **core:** advertise the caller's actual tool surface from `server/discover` — on an egress gateway it returned the flat backend projection, which is empty until some backend happens to start, instead of the `hangar_*` meta-API the caller would get from `tools/list` ([#606](https://github.com/mcp-hangar/mcp-hangar/pull/606))
- **core:** keep stdout clean on the stdio transport — structlog's default factory prints to stdout, so a log emitted before `setup_logging()` (a module-import-time one, for instance) corrupted the JSON-RPC stream and dropped the client's session ([#563](https://github.com/mcp-hangar/mcp-hangar/pull/563))
- **core:** honour `tool_access.mode: front_door` on `serve --http` — the gate lived only in the never-called `MCPServerFactory`, so a gateway configured front_door kept serving the `hangar_*` meta-API, lifecycle control included, to callers the mode exists to fail closed on ([#596](https://github.com/mcp-hangar/mcp-hangar/pull/596))
- **core:** advertise the SEP-2133 governance extensions on `serve --http`, via `get_capabilities` so both the handshake and the stateless `server/discover` surface carry them ([#595](https://github.com/mcp-hangar/mcp-hangar/pull/595))
- **core:** guard the `mcp` SDK pin with a metadata test on this line too — the v2 pin stays exact (`==2.0.0b2`, drift inside the beta series breaks `_sdk_compat` silently), mirroring the `<2` cap `main` needs for the v1 surface ([#561](https://github.com/mcp-hangar/mcp-hangar/pull/561))

### Security

- **security:** enforce per-tenant isolation -- require the token tenant claim in multi-tenant mode and derive the effective tenant solely from the validated token (never client-supplied), failing closed to prevent cross-tenant token use (#312) ([#371](https://github.com/mcp-hangar/mcp-hangar/pull/371))
- **security:** opt-in strict per-tenant audience binding (RFC 8707) -- when enabled, a token's `aud` must match the claimed tenant's resource, rejecting cross-tenant replay at the token layer; off by default (#373) ([#382](https://github.com/mcp-hangar/mcp-hangar/pull/382))
- **security:** wire auth components onto the application context at bootstrap so the API permission guard actually enforces RBAC -- previously `auth_components` was never set on the global context, so `_check_permission` read `None`, found no authz middleware, and fail-OPENed (returned early), letting any authenticated principal pass every check regardless of role ([#386](https://github.com/mcp-hangar/mcp-hangar/pull/386))
- **security:** bridge the authenticated caller identity into the tool-call path over streamable-HTTP (hangar_call now reads the principal from the request context), so per-tenant enforcement (canary routing, per-tenant tool withdrawal) is no longer silently bypassed with a null tenant over HTTP (#384) ([#387](https://github.com/mcp-hangar/mcp-hangar/pull/387))
- **security:** enforce `tool:invoke` authorization on the `hangar_call` tool path -- a principal lacking the permission is denied fail-closed (previously RBAC covered only the REST API, so any caller could invoke tools regardless of role) (#385) ([#389](https://github.com/mcp-hangar/mcp-hangar/pull/389))
- **security:** apply the per-tenant tool-access policy to the `hangar_tools`/`hangar_details` listing path -- the listing helpers filtered on the server-level policy only (no `member_id`), so a tool denied for a tenant was rejected on `hangar_call` yet still advertised in the listing (fail-OPEN on visibility); the listing now bridges the caller identity from the request principal and keys the resolver on the caller tenant, so listing and invocation agree ([#393](https://github.com/mcp-hangar/mcp-hangar/pull/393))
- **core:** Langfuse tracing now scrubs tool-call inputs and outputs by **default** (`scrub_inputs`/`scrub_outputs` default to true) — the exporter previously shipped full argument and result payloads to Langfuse unless explicitly disabled. Set them false to send full content for debugging. Found by the observability audit ([#531](https://github.com/mcp-hangar/mcp-hangar/pull/531))
- **core:** redact secret *values* (AWS/GitHub/Slack/Stripe keys, JWTs, bearer tokens, …) across the logging pipeline and the MCP-server log buffer. The value-level `OutputRedactor` is now a structlog processor (complementing the existing key-name redaction) and is applied to subprocess `stderr` at the source before it enters the buffer — so the `GET /mcp_servers/{id}/logs` API can no longer serve raw secrets that an MCP server printed to stderr. Long-string redaction stays off, so only recognizable token shapes are rewritten. Found by the observability audit ([#530](https://github.com/mcp-hangar/mcp-hangar/pull/530))
- **core:** L7 argument scanning now fails closed on un-serializable tool-call arguments (e.g. a circular reference) instead of raising — an unscannable payload is reported as a violation rather than crashing the evaluation — and skips serialization entirely when no argument rules are configured. Found by adversarial testing ([mcp-hangar-operator#53](https://github.com/mcp-hangar/mcp-hangar-operator/issues/53)) ([#529](https://github.com/mcp-hangar/mcp-hangar/pull/529))
- **security:** require `mcp>=1.28.1` to pull in the fix for CVE-2026-59950 (MCP Python SDK WebSocket server transport missing Host/Origin validation, HIGH). The published constraint was `mcp>=1.0.0`, so installs could still resolve a vulnerable SDK even though the dev lock had moved. ([#497](https://github.com/mcp-hangar/mcp-hangar/pull/497))
- **core:** validate WebSocket handshake `Origin`/`Host` at the Hangar ASGI edge before forwarding non-`/api/` connections to the SDK app (DNS-rebinding / cross-origin defense-in-depth, the CVE-2026-59950 class at our own trust boundary). Loopback is trusted; non-loopback is fail-closed — a present `Origin` must be allow-listed (`MCP_CORS_ORIGINS`), a missing one is allowed (non-browser client, auth still applies), and the `Host` must be in `MCP_TRUSTED_HOSTS` ([#498](https://github.com/mcp-hangar/mcp-hangar/pull/498))

## [1.6.3](https://github.com/mcp-hangar/mcp-hangar/compare/v1.6.2...v1.6.3) (2026-07-27)

### Fixed

- **core:** cap httpx below 1.0 on the v1 line too ([#619](https://github.com/mcp-hangar/mcp-hangar/pull/619)) ([74c1af7](https://github.com/mcp-hangar/mcp-hangar/commit/74c1af77394af5b7820c65e996425640931a0086))

## [1.6.2](https://github.com/mcp-hangar/mcp-hangar/compare/v1.6.1...v1.6.2) (2026-07-27)

### Fixed

- **core:** cap the mcp SDK pin at the v1 line ([#610](https://github.com/mcp-hangar/mcp-hangar/pull/610)) ([b862297](https://github.com/mcp-hangar/mcp-hangar/commit/b8622972e8c695b80535fa463bac0fa2fb3bd2a6)), closes [#561](https://github.com/mcp-hangar/mcp-hangar/issues/561)

## [1.6.1](https://github.com/mcp-hangar/mcp-hangar/compare/v1.6.0...v1.6.1) (2026-07-23)

### Added

- **core:** wire MCPEgressPolicy mode (Audit observes, Enforce blocks) ([#588](https://github.com/mcp-hangar/mcp-hangar/pull/588)) ([969b996](https://github.com/mcp-hangar/mcp-hangar/commit/969b99668c95d23c975f7f3affd02117230fa0bb))

### Changed

- **release:** force patch release 1.6.1 ([#593](https://github.com/mcp-hangar/mcp-hangar/pull/593)) ([44912b0](https://github.com/mcp-hangar/mcp-hangar/commit/44912b0a2eb4b30c4ca8a91b79fbd5efc3aae5ab))

## [1.6.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.5.1...v1.6.0) (2026-07-19)

### Added

- **core:** add L7 egress policy engine ([#526](https://github.com/mcp-hangar/mcp-hangar/pull/526)) ([575602d](https://github.com/mcp-hangar/mcp-hangar/commit/575602d1fc28b8f784169157470e2d6e3ddd2ec7))
- **core:** enforce L7 egress policy at the tool-invocation chokepoint ([#527](https://github.com/mcp-hangar/mcp-hangar/pull/527)) ([2d22ad9](https://github.com/mcp-hangar/mcp-hangar/commit/2d22ad99b1ad5458ae61d9e715941540916abb9d))
- **core:** receive compiled L7 egress policy over the REST API ([#528](https://github.com/mcp-hangar/mcp-hangar/pull/528)) ([0825a47](https://github.com/mcp-hangar/mcp-hangar/commit/0825a47bbf6888b4f88e126c942a824b060649a8))
- **observability:** add telemetry-health alerts (OTLP export + discovery validation) ([#541](https://github.com/mcp-hangar/mcp-hangar/pull/541)) ([393f492](https://github.com/mcp-hangar/mcp-hangar/commit/393f492229f0a02762153a0b6b3a1482f2bdc138))
- **observability:** trace the upstream call boundary (client spans, stdio propagation, sampler) ([#537](https://github.com/mcp-hangar/mcp-hangar/pull/537)) ([63bad07](https://github.com/mcp-hangar/mcp-hangar/commit/63bad0737305de542881974ca8cd4bd4682d177f))
- **observability:** wire transport message metrics; drop never-emitted pool/SSE gauges ([#540](https://github.com/mcp-hangar/mcp-hangar/pull/540)) ([9d3ed15](https://github.com/mcp-hangar/mcp-hangar/commit/9d3ed15764d0d013b850a59f8e055b426b0b4d0d))

### Changed

- **core:** collapse the vestigial enterprise plugin boundary ([#538](https://github.com/mcp-hangar/mcp-hangar/pull/538)) ([1813dcd](https://github.com/mcp-hangar/mcp-hangar/commit/1813dcdaecf29d4469ee0adb96d5555553a81ecc))
- **observability:** align tool-invocation spans to OTel GenAI/MCP semconv ([#539](https://github.com/mcp-hangar/mcp-hangar/pull/539)) ([d705c8f](https://github.com/mcp-hangar/mcp-hangar/commit/d705c8f20763137fda61bb7b537330c7d3357592))

### Fixed

- **core:** consolidate discovery metrics onto the scraped registry ([#534](https://github.com/mcp-hangar/mcp-hangar/pull/534)) ([d699ff0](https://github.com/mcp-hangar/mcp-hangar/commit/d699ff027d6259dd3751ecfb9f6434b1b3ffdb53))
- **core:** emit the cost-attribution metrics ([#535](https://github.com/mcp-hangar/mcp-hangar/pull/535)) ([275de80](https://github.com/mcp-hangar/mcp-hangar/commit/275de802cf6757b61f580d9252420ff479a6c30d))
- **core:** L7 argument scan fails closed on unserializable arguments ([#529](https://github.com/mcp-hangar/mcp-hangar/pull/529)) ([a14bb2e](https://github.com/mcp-hangar/mcp-hangar/commit/a14bb2ebc6580ab80098ef07fb1bd4242ac42b3a))
- **core:** redact secret values in logs and the log buffer ([#530](https://github.com/mcp-hangar/mcp-hangar/pull/530)) ([1374da8](https://github.com/mcp-hangar/mcp-hangar/commit/1374da8730e3c5af84bf32e0a9c128d863883170))
- **core:** scrub Langfuse tool inputs/outputs by default ([#531](https://github.com/mcp-hangar/mcp-hangar/pull/531)) ([98a2cb9](https://github.com/mcp-hangar/mcp-hangar/commit/98a2cb907692f77547c4a2ab639bc2e9dbf190c5))
- **core:** wire connections_active; delete the redundant connection metrics ([#536](https://github.com/mcp-hangar/mcp-hangar/pull/536)) ([81accc1](https://github.com/mcp-hangar/mcp-hangar/commit/81accc19a0680b2a1d5463b68420f77f5be490c5))
- **metrics:** stop poisoning the latency histogram; drop stream_id label ([#532](https://github.com/mcp-hangar/mcp-hangar/pull/532)) ([55abc52](https://github.com/mcp-hangar/mcp-hangar/commit/55abc5261627ad6101fddc229c271fea87fc1de0))
- **observability:** correlate logs with traces (trace_id/span_id) ([#533](https://github.com/mcp-hangar/mcp-hangar/pull/533)) ([29ea16b](https://github.com/mcp-hangar/mcp-hangar/commit/29ea16b09cf3fe751b0b296b1f0e608034e27c42))
- **observability:** mark a failed tool call's span as ERROR ([#544](https://github.com/mcp-hangar/mcp-hangar/pull/544)) ([43848e9](https://github.com/mcp-hangar/mcp-hangar/commit/43848e9349968219003a6975ea4056dd9098b5f7))
- **observability:** stop logging expected stdio shutdowns as errors ([#542](https://github.com/mcp-hangar/mcp-hangar/pull/542)) ([11ef2c7](https://github.com/mcp-hangar/mcp-hangar/commit/11ef2c7e5049c4951c68e16ca44db2419eb58a78))

### Security

- **core:** validate WebSocket handshake Origin/Host at the edge ([#524](https://github.com/mcp-hangar/mcp-hangar/pull/524)) ([403ec6c](https://github.com/mcp-hangar/mcp-hangar/commit/403ec6c700173faed3cf3da324993b0fc92d267c))

## [1.5.1](https://github.com/mcp-hangar/mcp-hangar/compare/v1.5.0...v1.5.1) (2026-07-16)

### Fixed

- **core:** resolve discovery/config review findings ([#481](https://github.com/mcp-hangar/mcp-hangar/issues/481), [#483](https://github.com/mcp-hangar/mcp-hangar/issues/483), [#484](https://github.com/mcp-hangar/mcp-hangar/issues/484)) ([#493](https://github.com/mcp-hangar/mcp-hangar/pull/493)) ([1600c54](https://github.com/mcp-hangar/mcp-hangar/commit/1600c543ecf6e3fa8d8af1b63f842c1339e46740))
- **repo:** add basic client scope to keycloak example realm so tokens carry sub ([#476](https://github.com/mcp-hangar/mcp-hangar/pull/476)) ([2c1e9f4](https://github.com/mcp-hangar/mcp-hangar/commit/2c1e9f4d3d673fb142cf5d8e217a8d8f89dc2da6))
- **security:** require mcp&gt;=1.28.1 (CVE-2026-59950) ([#497](https://github.com/mcp-hangar/mcp-hangar/pull/497)) ([5ba85d1](https://github.com/mcp-hangar/mcp-hangar/commit/5ba85d18c5c655d47092906e6577597528afa4dc))

## [1.5.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.4.0...v1.5.0) (2026-07-15)

### Added

- **cli:** add `auth bootstrap-admin` command (durable initial admin) ([#463](https://github.com/mcp-hangar/mcp-hangar/pull/463)) ([57b21fc](https://github.com/mcp-hangar/mcp-hangar/commit/57b21fc5816b8daf980c7272f4bae0fc94b3e9be)), closes [#451](https://github.com/mcp-hangar/mcp-hangar/issues/451) [#452](https://github.com/mcp-hangar/mcp-hangar/issues/452)
- **core:** add interceptor/invoke + phase-aware hooks, pinned to MCP `modelcontextprotocol/modelcontextprotocol#2624` ([#400](https://github.com/mcp-hangar/mcp-hangar/pull/400)) ([3a0e2b5](https://github.com/mcp-hangar/mcp-hangar/commit/3a0e2b5d4df67821aa743fb69ff64ab037b5b28e))
- **core:** add server/discover entry point backed by the per-tenant projection ([#407](https://github.com/mcp-hangar/mcp-hangar/pull/407)) ([6713cbd](https://github.com/mcp-hangar/mcp-hangar/commit/6713cbdef243977d36e3bfc30f24f4c3dc0c758d))
- **core:** configurable command-bus rate limit via config.yaml ([#398](https://github.com/mcp-hangar/mcp-hangar/pull/398)) ([a891496](https://github.com/mcp-hangar/mcp-hangar/commit/a89149610ebbf2337bc97253483840875e3339f8))
- **core:** emit task-lifecycle audit events (created/input_required/completed/failed/cancelled) ([#399](https://github.com/mcp-hangar/mcp-hangar/pull/399)) ([eb399bc](https://github.com/mcp-hangar/mcp-hangar/commit/eb399bcf8d0075721f95ba9a9abb9f3738d914f5))
- **observability:** meter OTLP export failures and document the tracing off-switch ([#419](https://github.com/mcp-hangar/mcp-hangar/pull/419)) ([515c57c](https://github.com/mcp-hangar/mcp-hangar/commit/515c57c7538e0c5959fd1f8fe566572592448637))
- **security:** atomically bootstrap the first API-key admin ([#456](https://github.com/mcp-hangar/mcp-hangar/pull/456)) ([9239705](https://github.com/mcp-hangar/mcp-hangar/commit/92397054a3d181c3ffe713a6c4022de6fad32250))

### Fixed

- **ci:** repair actionlint gate (broken action ref) and the YAML it flags ([#287](https://github.com/mcp-hangar/mcp-hangar/pull/287)) ([ee5de14](https://github.com/mcp-hangar/mcp-hangar/commit/ee5de144eea5c0fc3d8cb3dbefcbb7238c67b152))
- **cli:** accept --config after serve and fix generated Claude Desktop config ([#420](https://github.com/mcp-hangar/mcp-hangar/pull/420)) ([9068161](https://github.com/mcp-hangar/mcp-hangar/commit/9068161b4a6e0c2a72579841550ba081d3f440b5)), closes [#417](https://github.com/mcp-hangar/mcp-hangar/issues/417)
- **core:** clarify that mode:docker requires a host container CLI ([#430](https://github.com/mcp-hangar/mcp-hangar/pull/430)) ([732de25](https://github.com/mcp-hangar/mcp-hangar/commit/732de255652b8a579cac97392230457cf3acb25b)), closes [#429](https://github.com/mcp-hangar/mcp-hangar/issues/429)
- **core:** config.yaml.example uses mcp_servers: (loader requires it, not providers:) ([#458](https://github.com/mcp-hangar/mcp-hangar/pull/458)) ([498b312](https://github.com/mcp-hangar/mcp-hangar/commit/498b312fcf993041abfebd462688cf939faa4a0d)), closes [#457](https://github.com/mcp-hangar/mcp-hangar/issues/457)
- **core:** expose bootstrapped discovery through REST ([#442](https://github.com/mcp-hangar/mcp-hangar/pull/442)) ([1c2280c](https://github.com/mcp-hangar/mcp-hangar/commit/1c2280c2870a1718743b5f80af2090e2468093a4))
- **core:** fail fast when SQLite event store is unavailable ([#438](https://github.com/mcp-hangar/mcp-hangar/pull/438)) ([a1be5db](https://github.com/mcp-hangar/mcp-hangar/commit/a1be5db4a5f965d06adabf97dde0420c2ad2c59b))
- **core:** fail-fast on unwritable SQLite event store and add a durability readiness check ([#448](https://github.com/mcp-hangar/mcp-hangar/pull/448)) ([77f84cc](https://github.com/mcp-hangar/mcp-hangar/commit/77f84ccff9560a7d0eaf93a70f0fda9ce49a8d6a))
- **core:** group circuit breaker no longer blocks a healthy remaining member ([#426](https://github.com/mcp-hangar/mcp-hangar/pull/426)) ([0b9cdc8](https://github.com/mcp-hangar/mcp-hangar/commit/0b9cdc89b9e8b0de8aa1349aecc39ba4e10fa1eb)), closes [#425](https://github.com/mcp-hangar/mcp-hangar/issues/425)
- **core:** make EventStoreConfigurationError a ConfigurationError subclass ([#459](https://github.com/mcp-hangar/mcp-hangar/pull/459)) ([42cce1a](https://github.com/mcp-hangar/mcp-hangar/commit/42cce1ada6a2a70375a7e338405e7de2508defbb))
- **core:** re-pin interceptor schema to 99bc7c9 and reconcile SEP-2133 capability key ([#405](https://github.com/mcp-hangar/mcp-hangar/pull/405)) ([c972adf](https://github.com/mcp-hangar/mcp-hangar/commit/c972adf04aea89afe1fba49665e26f69ea5180b6))
- **core:** run discovery on a dedicated lifecycle loop ([#446](https://github.com/mcp-hangar/mcp-hangar/pull/446)) ([4eee12c](https://github.com/mcp-hangar/mcp-hangar/commit/4eee12c2efe9490e5b41602f07da6301c3df3b95))
- **core:** treat MCP tool result isError as a failure ([#427](https://github.com/mcp-hangar/mcp-hangar/pull/427)) ([8ed7405](https://github.com/mcp-hangar/mcp-hangar/commit/8ed7405abb7b56e4e5744e2d71b199178f73d60f)), closes [#423](https://github.com/mcp-hangar/mcp-hangar/issues/423)
- **core:** unblock concurrent cold-start waiters ([#440](https://github.com/mcp-hangar/mcp-hangar/pull/440)) ([9721349](https://github.com/mcp-hangar/mcp-hangar/commit/972134906eca086bea028c0ff5f77e6d631c7958))
- **core:** use supported lifecycle API during reload ([#441](https://github.com/mcp-hangar/mcp-hangar/pull/441)) ([98f09f1](https://github.com/mcp-hangar/mcp-hangar/commit/98f09f1cc5ec8949ce01cbf8660d809c406e76e1))
- **security:** read the Authorization header case-insensitively in JWTAuthenticator ([#472](https://github.com/mcp-hangar/mcp-hangar/pull/472)) ([7863848](https://github.com/mcp-hangar/mcp-hangar/commit/78638482741b7ca6e5b341a678453d6820ab3519))

## [1.4.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.3.0...v1.4.0) (2026-06-29)

### Added

- **core:** per-tenant canary and version routing for groups ([#283](https://github.com/mcp-hangar/mcp-hangar/pull/283)) ([3410801](https://github.com/mcp-hangar/mcp-hangar/commit/341080111b2368d95a1c61f097fb3c94159c6c68)), closes [#275](https://github.com/mcp-hangar/mcp-hangar/issues/275) [#226](https://github.com/mcp-hangar/mcp-hangar/issues/226)
- **core:** per-tenant tool digest pinning on the call path ([#276](https://github.com/mcp-hangar/mcp-hangar/pull/276)) ([0d2b2f2](https://github.com/mcp-hangar/mcp-hangar/commit/0d2b2f26161314bbe40e17d1669010f573e9bff2)), closes [#233](https://github.com/mcp-hangar/mcp-hangar/issues/233) [#226](https://github.com/mcp-hangar/mcp-hangar/issues/226)
- **observability:** activate availability and transport alerts ([#269](https://github.com/mcp-hangar/mcp-hangar/pull/269)) ([774cb8f](https://github.com/mcp-hangar/mcp-hangar/commit/774cb8f27b4ebce379ccee69dd462f97c3053770)), closes [#268](https://github.com/mcp-hangar/mcp-hangar/issues/268)
- **observability:** add governance dashboard and alerts for cost, security, and concurrency metrics ([#267](https://github.com/mcp-hangar/mcp-hangar/pull/267)) ([ced19dc](https://github.com/mcp-hangar/mcp-hangar/commit/ced19dc1d0dbe9cdb10636a0417689ee258a83d8)), closes [#261](https://github.com/mcp-hangar/mcp-hangar/issues/261) [#266](https://github.com/mcp-hangar/mcp-hangar/issues/266)
- **security:** bind token audience to resource URI (RFC 8707) ([#274](https://github.com/mcp-hangar/mcp-hangar/pull/274)) ([783b34b](https://github.com/mcp-hangar/mcp-hangar/commit/783b34b2837c379a66e6ae457e75185615ade1f8)), closes [#255](https://github.com/mcp-hangar/mcp-hangar/issues/255) [#253](https://github.com/mcp-hangar/mcp-hangar/issues/253)
- **security:** multi-issuer trust registry for OAuth Resource Server ([#273](https://github.com/mcp-hangar/mcp-hangar/pull/273)) ([2a7bd3e](https://github.com/mcp-hangar/mcp-hangar/commit/2a7bd3e6b02faa92fd7441fabe2a35d54e6c27b3)), closes [#254](https://github.com/mcp-hangar/mcp-hangar/issues/254) [#253](https://github.com/mcp-hangar/mcp-hangar/issues/253)

### Changed

- **observability:** remove dead ObservabilityMetrics registry ([#272](https://github.com/mcp-hangar/mcp-hangar/pull/272)) ([b93382a](https://github.com/mcp-hangar/mcp-hangar/commit/b93382a0ac3835cf102d3ee4595bd0fc974a7372)), closes [#271](https://github.com/mcp-hangar/mcp-hangar/issues/271)

### Fixed

- **core:** cost counters emit a doubled _total suffix ([#266](https://github.com/mcp-hangar/mcp-hangar/pull/266)) ([b05cd5c](https://github.com/mcp-hangar/mcp-hangar/commit/b05cd5c7800d5bd3f9dbbb297d6ec5104fd962d9)), closes [#265](https://github.com/mcp-hangar/mcp-hangar/issues/265)
- **core:** harden per-tenant digest pinning (per-server enforcement, tenant_id, tests) ([#280](https://github.com/mcp-hangar/mcp-hangar/pull/280)) ([066bf97](https://github.com/mcp-hangar/mcp-hangar/commit/066bf97dabdf3fb967d38fe9f8370b485c56e208)), closes [#278](https://github.com/mcp-hangar/mcp-hangar/issues/278) [#226](https://github.com/mcp-hangar/mcp-hangar/issues/226)
- **core:** select a group member on the invoke path ([#282](https://github.com/mcp-hangar/mcp-hangar/pull/282)) ([532afd8](https://github.com/mcp-hangar/mcp-hangar/commit/532afd86d43d771ef33c671cc28c2725bbb711df)), closes [#281](https://github.com/mcp-hangar/mcp-hangar/issues/281) [#275](https://github.com/mcp-hangar/mcp-hangar/issues/275)
- **observability:** align monitoring dashboards and alerts with mcp_server rename ([#263](https://github.com/mcp-hangar/mcp-hangar/pull/263)) ([db3f7a6](https://github.com/mcp-hangar/mcp-hangar/commit/db3f7a6e348b595516b57f94d70a1e557e47eb5e)), closes [#260](https://github.com/mcp-hangar/mcp-hangar/issues/260)
- **security:** reject non-string iss claim instead of raising 500 ([#279](https://github.com/mcp-hangar/mcp-hangar/pull/279)) ([ea1035f](https://github.com/mcp-hangar/mcp-hangar/commit/ea1035f6212e3d35a4f391c962048c7cba8e3bf4)), closes [#277](https://github.com/mcp-hangar/mcp-hangar/issues/277)

## [1.3.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.3...v1.3.0) (2026-06-23)

### Added

- **core:** add front_door fail-closed default for unauthenticated calls ([#242](https://github.com/mcp-hangar/mcp-hangar/pull/242)) ([b4d3200](https://github.com/mcp-hangar/mcp-hangar/commit/b4d32002a12e8fdb82b212dfdb13c5a83910a5bb)), closes [#236](https://github.com/mcp-hangar/mcp-hangar/issues/236)
- **core:** add runtime tool withdraw/restore mutation API ([#246](https://github.com/mcp-hangar/mcp-hangar/pull/246)) ([b72b43e](https://github.com/mcp-hangar/mcp-hangar/commit/b72b43e8f9ab4554245ad9f501c915c0c1243ac6)), closes [#235](https://github.com/mcp-hangar/mcp-hangar/issues/235)
- **core:** add tenant_id to CallerIdentity from JWT claim ([#238](https://github.com/mcp-hangar/mcp-hangar/pull/238)) ([0d85e36](https://github.com/mcp-hangar/mcp-hangar/commit/0d85e3669c50fa20e8e16a56c7bc123d9ec6cd4c)), closes [#228](https://github.com/mcp-hangar/mcp-hangar/issues/228)
- **core:** add ToolProjectionRegistry read-model ([#237](https://github.com/mcp-hangar/mcp-hangar/pull/237)) ([93b00c4](https://github.com/mcp-hangar/mcp-hangar/commit/93b00c4e8a4a45172356dfb7879ceea91cd31930)), closes [#230](https://github.com/mcp-hangar/mcp-hangar/issues/230)
- **core:** enforce tool withdrawal on the call path ([#243](https://github.com/mcp-hangar/mcp-hangar/pull/243)) ([40dcb77](https://github.com/mcp-hangar/mcp-hangar/commit/40dcb77ee37cb0e8bdb870ce8d9a3840c1618da5)), closes [#231](https://github.com/mcp-hangar/mcp-hangar/issues/231)
- **core:** flat per-tenant tool re-export in front_door mode ([#252](https://github.com/mcp-hangar/mcp-hangar/pull/252)) ([a8ecd17](https://github.com/mcp-hangar/mcp-hangar/commit/a8ecd178a0f9cc3c4b62fe6bb0b4fcc8c8093d93)), closes [#232](https://github.com/mcp-hangar/mcp-hangar/issues/232)
- **core:** populate tool withdrawal from config (reload-driven overlay) ([#245](https://github.com/mcp-hangar/mcp-hangar/pull/245)) ([ba1b6af](https://github.com/mcp-hangar/mcp-hangar/commit/ba1b6af4975a017a72c37f23a6bf3891d28599c4)), closes [#244](https://github.com/mcp-hangar/mcp-hangar/issues/244)
- **core:** populate ToolProjectionRegistry from tool discovery ([#250](https://github.com/mcp-hangar/mcp-hangar/pull/250)) ([02adbc5](https://github.com/mcp-hangar/mcp-hangar/commit/02adbc5c9f97beff8308d07bed72562232ce0e13)), closes [#248](https://github.com/mcp-hangar/mcp-hangar/issues/248)
- **core:** resolve member-scope tool policy on the live call path ([#241](https://github.com/mcp-hangar/mcp-hangar/pull/241)) ([152ca0e](https://github.com/mcp-hangar/mcp-hangar/commit/152ca0e123eee67493a5a15d41bb1631af27504e)), closes [#229](https://github.com/mcp-hangar/mcp-hangar/issues/229)
- **security:** advertise OAuth Protected Resource Metadata (RFC 9728) ([#257](https://github.com/mcp-hangar/mcp-hangar/pull/257)) ([d5a6089](https://github.com/mcp-hangar/mcp-hangar/commit/d5a6089f7f9fae1174f772d95f78bbb1e19867a7)), closes [#256](https://github.com/mcp-hangar/mcp-hangar/issues/256)

### Fixed

- **core:** bind caller identity on the MCP request path ([#249](https://github.com/mcp-hangar/mcp-hangar/pull/249)) ([af636cd](https://github.com/mcp-hangar/mcp-hangar/commit/af636cda084eacbcd22666c5f17ffeb3c79be156)), closes [#247](https://github.com/mcp-hangar/mcp-hangar/issues/247)
- **core:** propagate request context into batch worker threads ([#239](https://github.com/mcp-hangar/mcp-hangar/pull/239)) ([bad09d7](https://github.com/mcp-hangar/mcp-hangar/commit/bad09d78a354750be59c19c2324a4eaebe97c343)), closes [#227](https://github.com/mcp-hangar/mcp-hangar/issues/227)
- **core:** satisfy mypy and ruff format CI gates ([#258](https://github.com/mcp-hangar/mcp-hangar/pull/258)) ([d7a2a53](https://github.com/mcp-hangar/mcp-hangar/commit/d7a2a53825df6f86803a2402bf70eaba01ab1eda))

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

## [1.2.3](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.2...v1.2.3) (2026-06-23)

### Fixed

- **core:** add auth/tls/http config serialization to to_config_dict() ([#209](https://github.com/mcp-hangar/mcp-hangar/pull/209)) ([0df37d6](https://github.com/mcp-hangar/mcp-hangar/commit/0df37d6a8f6ad3b0287a6cd07c1e2e8895d1e6f2))
- **security:** make _sanitize() recursive to strip nested secrets ([#210](https://github.com/mcp-hangar/mcp-hangar/pull/210)) ([cfd2a0f](https://github.com/mcp-hangar/mcp-hangar/commit/cfd2a0f863e5d3c812ea6a4d7e79657e287c91b6)), closes [#206](https://github.com/mcp-hangar/mcp-hangar/issues/206)

## [1.2.2](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.1...v1.2.2) (2026-05-17)

### Changed

- **core:** absorb enterprise/ into src/mcp_hangar/ ([#201](https://github.com/mcp-hangar/mcp-hangar/pull/201)) ([010f2a0](https://github.com/mcp-hangar/mcp-hangar/commit/010f2a01f55130596a8934f56f5fcf65bff05229))
- **docs:** move adr/AGENTS.md to docs/internal/ADR_AGENTS.md ([4be7c4f](https://github.com/mcp-hangar/mcp-hangar/commit/4be7c4f2172295e5dff87bd47d3c6ee3d9f42c2e))

### Fixed

- **core:** remove CLA nav entry and fix ruff formatting in cef_formatter ([753d485](https://github.com/mcp-hangar/mcp-hangar/commit/753d485617781b3bfcf7c06c3540d3e0dfd071e9))

## [1.2.1](https://github.com/mcp-hangar/mcp-hangar/compare/v1.2.0...v1.2.1) (2026-05-11)

### Changed

- **core:** rename ALLOW_DEGRADED to ALLOW_UNVERIFIED in DigestUnknownPolicy ([#189](https://github.com/mcp-hangar/mcp-hangar/pull/189)) ([00a44b4](https://github.com/mcp-hangar/mcp-hangar/commit/00a44b4fddae32fec1538c6b0517eed6c1311756)), closes [#175](https://github.com/mcp-hangar/mcp-hangar/issues/175)

### Fixed

- **core:** disambiguate interceptors/list instance names ([#190](https://github.com/mcp-hangar/mcp-hangar/pull/190)) ([4e4a86c](https://github.com/mcp-hangar/mcp-hangar/commit/4e4a86cc6a89ce3115b91709f6af534f6ccb2403)), closes [#176](https://github.com/mcp-hangar/mcp-hangar/issues/176)
- **core:** replace json.dumps with RFC 8785 JCS canonicalization in compute_tool_digest ([#186](https://github.com/mcp-hangar/mcp-hangar/pull/186)) ([5626ef8](https://github.com/mcp-hangar/mcp-hangar/commit/5626ef87643714b2fa80af56ebb7f0b2c0270a76))
- **core:** treat empty values as absent in tool digest computation ([#188](https://github.com/mcp-hangar/mcp-hangar/pull/188)) ([fc93d13](https://github.com/mcp-hangar/mcp-hangar/commit/fc93d136887ba3cc86b4a50f47e843384e57fd9b)), closes [#173](https://github.com/mcp-hangar/mcp-hangar/issues/173)

## [1.2.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.1.0...v1.2.0) (2026-05-11)

### Added

- **ci:** add actionlint workflow to validate workflow YAML ([#115](https://github.com/mcp-hangar/mcp-hangar/pull/115)) ([1c887d1](https://github.com/mcp-hangar/mcp-hangar/commit/1c887d1a2748b1e22eb56765603e4b0ee5d64454)), closes [#111](https://github.com/mcp-hangar/mcp-hangar/issues/111)
- **core:** add ADR-004 digest pinning domain types and standalone validator ([#123](https://github.com/mcp-hangar/mcp-hangar/pull/123)) ([ad1b179](https://github.com/mcp-hangar/mcp-hangar/commit/ad1b1799d47d09d2ba7f8498c198a0415d78ee2f))
- **core:** add hook-based event model and interceptors/list ([#136](https://github.com/mcp-hangar/mcp-hangar/pull/136)) ([aa4f05c](https://github.com/mcp-hangar/mcp-hangar/commit/aa4f05c61cebbf382da79d5779e290837f041964)), closes [#120](https://github.com/mcp-hangar/mcp-hangar/issues/120) [#118](https://github.com/mcp-hangar/mcp-hangar/issues/118)
- **core:** add IMutator, MutatorPipeline, and ResponseTruncator ([#137](https://github.com/mcp-hangar/mcp-hangar/pull/137)) ([750541b](https://github.com/mcp-hangar/mcp-hangar/commit/750541bba2cc7d1b0b5844dd05317748dadb1d88)), closes [#121](https://github.com/mcp-hangar/mcp-hangar/issues/121) [#118](https://github.com/mcp-hangar/mcp-hangar/issues/118)
- **core:** add wildcard event subscription patterns (ADR-005 P1) ([#138](https://github.com/mcp-hangar/mcp-hangar/pull/138)) ([a7ae07d](https://github.com/mcp-hangar/mcp-hangar/commit/a7ae07d49dd22c4df4573331f613044a82ce550d)), closes [#122](https://github.com/mcp-hangar/mcp-hangar/issues/122)

### Fixed

- **ci:** remove PR-only checks from required status checks on main ([#114](https://github.com/mcp-hangar/mcp-hangar/pull/114)) ([3e6ddd4](https://github.com/mcp-hangar/mcp-hangar/commit/3e6ddd4a520b724c2c9ce42b0c94c67ede497aea)), closes [#110](https://github.com/mcp-hangar/mcp-hangar/issues/110)
- **core:** register interceptors/list route on bootstrap FastMCP instance ([#154](https://github.com/mcp-hangar/mcp-hangar/pull/154)) ([069329c](https://github.com/mcp-hangar/mcp-hangar/commit/069329cc9325b4edc1cc7562567c431416622a63)), closes [#151](https://github.com/mcp-hangar/mcp-hangar/issues/151)
- **docs:** add cross-repo operator prerequisites for Kubernetes recipes ([#146](https://github.com/mcp-hangar/mcp-hangar/pull/146)) ([e9b5e69](https://github.com/mcp-hangar/mcp-hangar/commit/e9b5e6926f288b032485524c4196058fae443f18)), closes [#127](https://github.com/mcp-hangar/mcp-hangar/issues/127)
- **docs:** clean up Provider to McpServer artifacts in guides ([#148](https://github.com/mcp-hangar/mcp-hangar/pull/148)) ([f5d12d2](https://github.com/mcp-hangar/mcp-hangar/commit/f5d12d225f8ef5b6e4005bb2788ea4da4253bdbe)), closes [#133](https://github.com/mcp-hangar/mcp-hangar/issues/133)
- **docs:** correct hangar_call format, CLI usage, and endpoints in cookbook ([#143](https://github.com/mcp-hangar/mcp-hangar/pull/143)) ([b24f951](https://github.com/mcp-hangar/mcp-hangar/commit/b24f951b944317c79d57bdd3c7a62add64d125b0)), closes [#125](https://github.com/mcp-hangar/mcp-hangar/issues/125)
- **docs:** correct stale metrics section in OBSERVABILITY.md ([#141](https://github.com/mcp-hangar/mcp-hangar/pull/141)) ([936be63](https://github.com/mcp-hangar/mcp-hangar/commit/936be637ae05c6863ff9b52da63e3ffbc412aba0)), closes [#135](https://github.com/mcp-hangar/mcp-hangar/issues/135)
- **docs:** drop phantom CLI subcommands and fix stale metric names ([#149](https://github.com/mcp-hangar/mcp-hangar/pull/149)) ([edf28f2](https://github.com/mcp-hangar/mcp-hangar/commit/edf28f260a09fd7eb6c3fd13c1372fdafd6a1ace)), closes [#134](https://github.com/mcp-hangar/mcp-hangar/issues/134)
- **docs:** drop phantom config blocks from cookbook recipes ([#144](https://github.com/mcp-hangar/mcp-hangar/pull/144)) ([8df8955](https://github.com/mcp-hangar/mcp-hangar/commit/8df89554acf33d763bcfb060daa3b20acd679502)), closes [#126](https://github.com/mcp-hangar/mcp-hangar/issues/126)
- **docs:** drop phantom endpoints from REST_API, WEBSOCKETS, LOG_STREAMING ([#145](https://github.com/mcp-hangar/mcp-hangar/pull/145)) ([b178ee8](https://github.com/mcp-hangar/mcp-hangar/commit/b178ee87e74284951883d473e7c0408f58e58c12)), closes [#132](https://github.com/mcp-hangar/mcp-hangar/issues/132)
- **docs:** fix leftover drift in cookbook recipes 02/03/04 ([#155](https://github.com/mcp-hangar/mcp-hangar/pull/155)) ([f1826f3](https://github.com/mcp-hangar/mcp-hangar/commit/f1826f35d9d47336f48b04bf99f856c1e0e5ada6)), closes [#152](https://github.com/mcp-hangar/mcp-hangar/issues/152)
- **docs:** provider to mcp_server terminology cleanup in cookbook ([#147](https://github.com/mcp-hangar/mcp-hangar/pull/147)) ([e6d0d1b](https://github.com/mcp-hangar/mcp-hangar/commit/e6d0d1b95601560329f099c878c783bbf97084bc)), closes [#129](https://github.com/mcp-hangar/mcp-hangar/issues/129)
- **docs:** replace broken prerequisites with in-repo provider_math image ([#142](https://github.com/mcp-hangar/mcp-hangar/pull/142)) ([d318671](https://github.com/mcp-hangar/mcp-hangar/commit/d3186714ee3159a8138c2055e2eed57c8da0f15b)), closes [#128](https://github.com/mcp-hangar/mcp-hangar/issues/128)
- **docs:** REST_API.md auth method, empty sections, discovery prereq ([#156](https://github.com/mcp-hangar/mcp-hangar/pull/156)) ([aa34274](https://github.com/mcp-hangar/mcp-hangar/commit/aa3427452c263cc1b53d2a0c91f8c06bf513083d)), closes [#153](https://github.com/mcp-hangar/mcp-hangar/issues/153)
- **observability:** restore set_tracer_provider call broken by global rename ([#150](https://github.com/mcp-hangar/mcp-hangar/pull/150)) ([41b5be9](https://github.com/mcp-hangar/mcp-hangar/commit/41b5be97219eb642f9fa40ceeb3c91ed6d2414c9))

### Security

- **ci:** scope dependabot-automerge pull_request_target to main ([#116](https://github.com/mcp-hangar/mcp-hangar/pull/116)) ([eb4b53b](https://github.com/mcp-hangar/mcp-hangar/commit/eb4b53b47e62b8cde4cd165aa38c4c85ddcdcbc9)), closes [#112](https://github.com/mcp-hangar/mcp-hangar/issues/112)

## [1.1.0](https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.3...v1.1.0) (2026-05-10)

### Added

- **observability:** wire observability and compliance loop end-to-end ([#107](https://github.com/mcp-hangar/mcp-hangar/pull/107)) ([5dbdfc9](https://github.com/mcp-hangar/mcp-hangar/commit/5dbdfc9374283835fffdff05e1d4cd9217a86455)), closes [#106](https://github.com/mcp-hangar/mcp-hangar/issues/106)

### Fixed

- **ci:** bump actions/add-to-project from v1 to v2 ([#100](https://github.com/mcp-hangar/mcp-hangar/pull/100)) ([b248fa1](https://github.com/mcp-hangar/mcp-hangar/commit/b248fa152a09a9686141daa41f9d9dd6059ca699))
- **ci:** fix release notes %0A encoding, duplicate What's Changed, and HTML entities ([#109](https://github.com/mcp-hangar/mcp-hangar/pull/109)) ([7d2e691](https://github.com/mcp-hangar/mcp-hangar/commit/7d2e6915bf6e84265174c852754e01b1be731395))

## [1.0.3](https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.2...v1.0.3) (2026-05-10)

### Changed

- clean up Provider -&gt; McpServer legacy shims in events and commands ([e4aa6db](https://github.com/mcp-hangar/mcp-hangar/commit/e4aa6dbbbd179469b7eb095935226c2a57e9a77e))
- eliminate all static enterprise imports in core (TASK-P0-2, TASK-PRECOMMIT-FIX) ([53c2b73](https://github.com/mcp-hangar/mcp-hangar/commit/53c2b73780dd818263204e8923ef8f518e6be52a))
- reuse thread-local event loop in approval gate instead of creating per call ([a7a4338](https://github.com/mcp-hangar/mcp-hangar/commit/a7a4338e3314e469195e1f0d62a5c17899531f9e))

### Fixed

- add TTL for failover saga states to prevent ghost entries ([3757c3f](https://github.com/mcp-hangar/mcp-hangar/commit/3757c3f15f89caf8806a76281be4439375a7a676))
- **ci:** drop strict flag from pip-audit to allow skip-editable ([#93](https://github.com/mcp-hangar/mcp-hangar/pull/93)) ([2ca2d1c](https://github.com/mcp-hangar/mcp-hangar/commit/2ca2d1c2f67c8f1364cc1379cd1c7cc27d798b97))
- **ci:** fix crlf parsing and relax commitlint subject-case ([#72](https://github.com/mcp-hangar/mcp-hangar/pull/72)) ([664323f](https://github.com/mcp-hangar/mcp-hangar/commit/664323fd8f28caee705d3d4715daa3c6ba19f9c1))
- **ci:** skip editable installs in pip-audit to avoid self-lookup ([#85](https://github.com/mcp-hangar/mcp-hangar/pull/85)) ([e707862](https://github.com/mcp-hangar/mcp-hangar/commit/e707862b025d353c8678444d87f124567b0abc1f))
- **ci:** skip pr-title and commitlint on release-please branches ([#88](https://github.com/mcp-hangar/mcp-hangar/pull/88)) ([9045b50](https://github.com/mcp-hangar/mcp-hangar/commit/9045b50f1704c125e825144eba79da12ef736436))
- consolidate auth context storage to single canonical write path ([bc1d42b](https://github.com/mcp-hangar/mcp-hangar/commit/bc1d42b3a0431ad802b92b0d163b9e2851a12a20))
- **docs:** add blank lines before lists in ADR-004 and ADR-005 (MD032) ([d2f046b](https://github.com/mcp-hangar/mcp-hangar/commit/d2f046b3f1668afe78f8fade1c64cd6b1e497a59))
- **docs:** resolve markdownlint MD032 and mkdocs strict-mode link warnings ([494a4cd](https://github.com/mcp-hangar/mcp-hangar/commit/494a4cd4f8ab261a482b70d10ff81cfb15a7b846))
- **docs:** use glob exclusion for changelog in markdownlint workflow ([#92](https://github.com/mcp-hangar/mcp-hangar/pull/92)) ([56886a6](https://github.com/mcp-hangar/mcp-hangar/commit/56886a63b9c38f8a76ba0e552d0b20edf2fedb3d))
- restore TracerProvider import broken by Provider-to-McpServer rename ([fcb204a](https://github.com/mcp-hangar/mcp-hangar/commit/fcb204a4d5861901b55b64fbf3aef41bcc0e2bb9))
- **tests:** align test assertions with McpServerMode enum and tools.list_names() API ([b67b5c3](https://github.com/mcp-hangar/mcp-hangar/commit/b67b5c3ac0f770497f4e2d45333dcf51fe2c3e3b))

## [1.0.2](https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.1...v1.0.2) (2026-04-24)

### Added

- `tests/integration/test_e2e_mcp_flow.py` -- end-to-end integration tests for full MCP server lifecycle.
- `tests/security/test_identity_network.py` -- security regression tests for identity extraction and network hardening.

### Changed

- **Enterprise Boundary**: server bootstrap and router code now resolve optional enterprise integrations through a single core provider boundary in `server/bootstrap/enterprise.py` instead of scattered direct `enterprise.*` imports. The boundary supports entry-point discovery when available and a monorepo-safe fallback for local development.
- **Unified Auth Enforcement**: HTTP and WebSocket auth now share the same core enforcement path in `server/api/middleware.py`, including trusted proxy resolution, `?token=` WebSocket bearer mapping, auth context propagation, and consistent 401/403/1008 failures.
- **CSRF Scope**: CSRF protection now targets browser-style session suspension requests instead of all mutating API routes. Browser hints (`Origin`, `Referer`, `Cookie`) plus `X-Requested-With` are used to distinguish SPA/browser requests from API clients.

### Security

- Browser-originated `POST /sessions/{id}/suspend` now requires `X-Requested-With`, while API key / bearer / non-browser clients remain unaffected.
- Direct server-layer `from enterprise` imports were removed from the bootstrap/router path, shrinking the core-to-enterprise attack surface and making the boundary auditable in one place.

## [1.0.1](https://github.com/mcp-hangar/mcp-hangar/compare/v1.0.0...v1.0.1) (2026-04-17)

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

## [1.0.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.12.0...v1.0.0) (2026-04-11)

First stable release. All public APIs are now covered by semantic versioning guarantees.

### Added

- **Enterprise Module System** (Phases 47, BSL 1.1): ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
  - `LicenseTier` enum (COMMUNITY, PRO, ENTERPRISE) with `LicenseValidator` HMAC-SHA256 key validation
  - `EnterpriseComponents` dataclass and `load_enterprise_modules()` bootstrap integration
  - License tier gating: enterprise features activate based on license key; all failure modes fall back to COMMUNITY
  - HMAC signing secret configurable via `HANGAR_LICENSE_HMAC_SECRET` environment variable (no longer hardcoded)

- **Capability Declaration and Enforcement** (Phases 38-41): ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
  - `McpServerCapabilities` value object with network, filesystem, environment, tool, and resource declarations
  - `from_dict()` factory and config.yaml integration for capability blocks
  - Kubernetes CRD types for capabilities with reconciler propagation to status
  - `NetworkPolicyBuilder` pure function generating Kubernetes NetworkPolicy from declared egress rules
  - Docker capabilities-aware network mode in `DockerLauncher`
  - `ViolationType` and `ViolationSeverity` enums with Prometheus violations counter
  - `ViolationRecord` CRD type and `ViolationDetected` condition in operator reconciler
  - CEL admission validation and `ExpectedTools` field in MCPServer CRD
  - Wildcard egress override audit warning event

- **Behavioral Profiling** (Phases 42-44): ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
  - `IBehavioralProfiler`, `IBaselineStore`, `IDeviationDetector` contracts with null implementations
  - `BehavioralMode` enum, `NetworkObservation` value object, `BehavioralModeChanged` event
  - SQLite-backed `BaselineStore` for behavioral profiling data
  - `BehavioralProfiler` facade with enterprise bootstrap conditional loading
  - `DeviationDetector` with 3 detection rules (new destination, protocol drift, frequency anomaly)
  - ENFORCING mode support with event handler integration

- **Network Connection Monitoring** (Phase 43): ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
  - `/proc/net/tcp` and `ss` output parsers for connection tracking
  - `DockerNetworkMonitor` with container label injection
  - `K8sNetworkMonitor` with audit events and pod exec fallback
  - `ConnectionLogWorker` with monitor orchestration, bootstrap wiring, and config parsing

- **Tool Schema Drift Detection** (Phase 45): ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
  - MIT domain types for tool schema change tracking
  - `SchemaTracker` BSL class with SQLite storage and bootstrap wiring
  - `ToolSchemaChangeHandler` with event-driven schema diff detection

- **Resource Monitoring** (Phase 46):
  - `ResourceStore` with CRUD, baseline tracking, and pruning
  - `ResourceMonitorWorker` with bootstrap wiring and config integration
  - `BehavioralReportGenerator` with JSON and PDF export (via fpdf2)
  - Behavioral report REST endpoint with enterprise 403 gating

- **OpenTelemetry Governance Telemetry** (Phases 31-34): ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
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

- **Cloud Connector** (uplink to hangar-cloud SaaS): ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
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

- **Development Status**: Promoted from Beta to Production/Stable ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
- **HMAC secret**: License key signing secret now read from `HANGAR_LICENSE_HMAC_SECRET` environment variable with dev-only fallback ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
- **Documentation URLs**: Consolidated to `mcp-hangar.io` (removed stale `github.io` references) ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))

### Fixed

- Cloud connector: tool arguments no longer leak to cloud telemetry endpoint ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
- Cloud connector: infinite retry loop on failed registration replaced with bounded retry + dormant mode ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))
- Docker Compose quickstart example: removed deprecated `version` key ([#33](https://github.com/mcp-hangar/mcp-hangar/pull/33))

## [0.12.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.11.0...v0.12.0) (2026-03-23)

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

### Changed

- Rate limit metrics exported to Prometheus (RESL-04)
- BLE001 exception hygiene across codebase (EXCP-02)
- Fuzz tests for input validation (TEST-02)

### Fixed

- Thread-safety regression in `groups.py` rebalance
- Group member weight/priority defaults and strategy passthrough on update
- Group strategy enum, groups dict wiring, `normalizePath` trailing slash
- Missing `strategy` field in `UpdateGroupCommand`

## [0.11.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.10...v0.11.0) (2026-03-08)

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

### Changed

- Discovery pipeline now validates commands before MCP server registration
- `StdioClient` ordering invariant documented with regression tests

### Fixed

- **Concurrency Safety**: `McpServerGroup` lock hierarchy violation (CONC-01) resolved
- **invoke_tool() Refresh**: Split into two-lock-cycle pattern (CONC-03) to avoid holding locks during I/O
- **ensure_ready()/_start()**: Restructured with `threading.Event` coordination for safer startup
- **Exception Hygiene**: All exception catches across domain, application, infrastructure, and server layers
  narrowed and annotated -- no more bare `except Exception` without justification
- **Type Safety**: Fixed mypy errors in `rate_limiter`, `gc`, and `docker_source`

## [0.10.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.9.0...v0.10) (2026-03-01)

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

## [0.9.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.8.0...v0.9.0) (2026-02-15)

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

## [0.8.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.7.0...v0.8.0) (2026-02-15)

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

## [0.7.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.7...v0.7.0) (2026-02-08)

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

## [0.6.7](https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.6...v0.6.7) (2026-02-06)

### Fixed

- **ConfigReloadWorker tests**: Fixed timing issues in integration tests
  - `test_watchdog_detects_file_modification`: Increased watchdog initialization time and debounce wait
  - `test_multiple_rapid_changes_debounced_in_watchdog`: Added explicit polling interval configuration
  - `test_polling_detects_file_modification`: Ensured sufficient mtime difference for detection
- **CLI add MCP server test**: Fixed assertion to accept both uvx and npx package names
  - Test now correctly validates `mcp-server-fetch` (uvx) or `@modelcontextprotocol/server-fetch` (npx)

## [0.6.6](https://github.com/mcp-hangar/mcp-hangar/releases/tag/v0.6.6) (2026-02-06)

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

## 0.6.5 (2026-02-03)

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

## 0.6.4 (2026-02-03)

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

## [0.6.3](https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.2...v0.6.3) (2026-02-01)

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

## [0.6.2](https://github.com/mcp-hangar/mcp-hangar/compare/v0.6.0...v0.6.2) (2026-01-31)

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

## [0.6.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.5.0...v0.6.0) (2026-01-31)

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

## [0.5.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.4.0...v0.5.0) (2026-01-29)

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

## [0.4.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.3.1...v0.4.0) (2026-01-29)

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

## [0.3.1](https://github.com/mcp-hangar/mcp-hangar/compare/v0.3.0...v0.3.1) (2026-01-24)

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

## [0.3.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.2.3...v0.3.0) (2026-01-21)

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

### Changed

- **Breaking**: `bootstrap()` now accepts optional `config_dict` parameter for programmatic config
- **Dependencies**: Updated minimum Python version requirement clarified as 3.11+

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

## [0.2.3](https://github.com/mcp-hangar/mcp-hangar/releases/tag/v0.2.3) (2026-01-20)

### Fixed

- **Core**: Improved error diagnostics for MCP server startup failures - stderr from container/subprocess is now included in error messages instead of generic "unknown error"
- **Core**: `StdioClient` now captures and propagates stderr to error messages when process dies
- **Core**: `MCP Server._handle_start_failure()` now receives actual exception instead of None

## 0.2.2 (2026-01-19)

### Fixed

- **CI**: Re-enable mypy type checking in CI with gradual adoption configuration
- **Core**: Configure mypy with relaxed settings for gradual type safety improvement
- **Core**: Disable specific mypy error codes during transition period (union-attr, arg-type, override, etc.)

### Technical Debt Notes

The following items are documented technical debt introduced to enable CI:

- **Mypy not in strict mode**: Currently using relaxed settings with many error codes disabled. Plan to gradually enable stricter checking. See `pyproject.toml` for full list of disabled error codes.
- **Docker push disabled**: Requires organization package write permissions in GitHub settings.

## [0.2.1](https://github.com/mcp-hangar/mcp-hangar/compare/v0.2.0...v0.2.1) (2026-01-18)

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

## [0.2.0](https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.4...v0.2.0) (2026-01-18)

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

## [0.1.4](https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.3...v0.1.4) (2026-01-16)

### Added

- **Event Store Implementation**: Full Event Sourcing support with persistence
  - `IEventStore` interface with SQLite and In-Memory implementations
  - Optimistic concurrency control for concurrent event appends
  - Event serialization/deserialization with JSON support
  - Integration with EventBus for automatic event persistence
  - `publish_to_stream()` and `publish_aggregate_events()` methods
  - Configurable via `event_store` section in config.yaml
  - Complete test coverage (33 new tests)

## [0.1.3](https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.2...v0.1.3) (2026-01-14)

### Skipped

## [0.1.2](https://github.com/mcp-hangar/mcp-hangar/compare/v0.1.1...v0.1.2) (2026-01-13)

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

## [0.1.1](https://github.com/mcp-hangar/mcp-hangar/releases/tag/v0.1.1) (2026-01-12)

### Added

- **Observability Module**: Comprehensive monitoring and tracing support ([#10](https://github.com/mcp-hangar/mcp-hangar/pull/10))
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

## 0.1.0 (2025-12-16)

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
- GitHub Actions CI/CD for linting and testing (Python 3.11-3.14) ([#15](https://github.com/mcp-hangar/mcp-hangar/pull/15))
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

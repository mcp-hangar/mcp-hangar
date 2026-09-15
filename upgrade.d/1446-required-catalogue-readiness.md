### a front door can wait for its catalogue before it is ready

Opt-in: nothing changes unless you add `tool_access.required_catalogue`, and it
only takes effect with `tool_access.mode: front_door`.

```yaml
tool_access:
  mode: front_door
  required_catalogue:
    servers: [payments, search-pool]
    retry_for_s: 600
```

- `retry_for_s`, 600 by default, is a window that opens when the replica first
  applies its configuration. Inside it, `/health/ready` answers 503 until the
  boot warm-up has projected every listed server once. When they all have
  been, or when the window ends, readiness stops depending on the catalogue
  and goes back to today's rule. A replica is held out of the Service for at
  most `retry_for_s`, even if a listed backend never comes back.
- The window counts from that first apply, before the rest of boot and the
  warm-up, so set `retry_for_s` to cover boot plus the warm-up. If they
  outlast it, the retry still gives each missing server one attempt before it
  ends; readiness does not wait for that attempt.
- The readiness endpoint is unauthenticated, so its `catalogue` field reports
  counts and state only: `complete`, `holds_readiness`, `required`,
  `projected`, `missing_count`, `not_retried_count`, and `retry`.
- The missing ids, and the reason the retry will not start a server, are
  logged in a `required_catalogue_waiting` line each time they change, and
  `hangar_health` returns them under `catalogue`. Both keep reporting after
  the window ends.
- Within the window, a server the warm-up could not start is retried. The
  retry starts a server the way a call does, so a dead server waits out its
  backoff. It never starts a server that is `dead` for `given_up` or
  `capability_blocked`, it leaves a `degraded` server to the recovery saga, and
  it never starts a server this replica has already projected, so a server
  stopped for being idle stays stopped. Each attempt writes a
  `required_catalogue_retry` log line and one sample of
  `mcp_hangar_catalogue_retries_total{mcp_server, outcome}`.
- The retry ends in a final state: `finished` once the list is met,
  `blocked` as soon as every server still missing is one it may not start,
  `exhausted` when the window ends, and `stopped` at shutdown or when a
  reload removes the list. After `blocked`, readiness still waits for the rest
  of the window.
- Once every listed server has been projected, readiness never depends on the
  catalogue again: a backend that stops, goes idle or fails later does not make
  the replica not ready.
- A group id is satisfied once any one of its members has been projected. A
  member defined only inline in its group can be listed too.
- The block is checked at load, from a file and from a dict alike. These
  refuse the configuration:
  - an id that is not a server or group in `mcp_servers`;
  - a group with no members;
  - a key other than `servers` and `retry_for_s`;
  - a `retry_for_s` that is not a number above 0;
  - with a `coordination:` block or a shared storage backend, a listed server
    in a local mode (`subprocess`, `docker`, `container`, `podman`), or a group
    whose members all are. Only the replica holding the management lease may
    start one, so the others could never project it. Use `remote` mode for a
    server every replica must serve.
  - A persistence backend registered by a plugin is treated as shared, so a
    local-mode server is refused there too, as a precaution.
  - A single-replica deployment on a shared backend, such as `postgresql`,
    cannot require a local-mode server either: the configuration cannot know
    how many replicas will run it.

  In `egress` it is checked and then ignored.
- A reload checks the block like any other key, and never moves the window.
  Inside the window, a reload replaces the list, and one that removes the
  block releases the wait. A replica that has met its list stays ready. After
  the window has ended, or on a replica that booted without a list, a reload
  that adds a required server does not hold readiness and does not start a
  retry: the server is reported in the log and in `hangar_health`, and is
  started by a call or a deliberate start.

**A call's cold start now follows the call rules**, in every topology. The
batch executor starts a cold or dead server as a call, not as a deliberate
start, so a server that became capability-blocked, or went back into its
backoff, after the executor checked it is not started by the call. The call
is refused with the code the executor's own check gives the same condition:
`CircuitBreakerOpen` inside a backoff, `CannotStartMcpServerError` for a
capability block.

**If readiness stays 503.** Read the `required_catalogue_waiting` log line, or
`hangar_health`, for the ids. A server under `not_retried` is one Hangar gave up
on or blocked for a capability drift. Fix it, then start it deliberately
(`hangar_start`, or a start through the REST API), or take it off the list;
otherwise readiness falls back when the window ends.

**Probe timings.** A replica can now stay not ready for up to `retry_for_s`
after it starts. A failing readiness probe does not restart a pod, but a
rollout waits for it, so keep the Deployment's `progressDeadlineSeconds` above
`retry_for_s`.

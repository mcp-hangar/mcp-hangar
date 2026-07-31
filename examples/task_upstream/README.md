# Task-emitting upstream (MCP SDK v2, Tasks extension)

A small MCP server that answers a tool call with a **task handle** instead of a
result, and serves the native `tasks/*` lifecycle. It exists to exercise
mcp-hangar's governed task relay (ADR-014) end to end against a real upstream
rather than a mock.

The upstream stays deliberately on the **older** Tasks design — it answers
`tasks/get` with a status and keeps the payload behind `tasks/result`. Hangar
serves the SEP-2663 wire downstream and bridges the difference, so this example
exercises the bridge, which is the part most likely to rot silently.

It is the only example here pinned to the **v2 pre-release SDK** (`mcp==2.0.0b2`).
Every other example resolves the stable 1.x line, which has no Tasks extension.

| file | what it is |
| --- | --- |
| `server.py` | the upstream: `long_job`, `long_job_consent`, plus a plain `echo` |
| `Dockerfile` / `requirements.txt` | container image, SDK pinned exactly |
| `docker-compose.yml` | Hangar + upstream, wired together |
| `config.yaml` | registers the upstream with Hangar as a `remote` server |
| `k8s.yaml` | the same upstream on Kubernetes via the `MCPServer` CRD |
| `smoke_upstream.py` | drives the upstream **directly** — the upstream's own contract |
| `drive_relay.py` | drives the full relay lifecycle **through Hangar** |

## Run it

```bash
HANGAR_IMAGE=ghcr.io/mcp-hangar/mcp-hangar:2.0.0-rc.3 \
  docker compose -f examples/task_upstream/docker-compose.yml up --build

python examples/task_upstream/smoke_upstream.py --url http://127.0.0.1:8081/mcp  # upstream alone
python examples/task_upstream/drive_relay.py                                     # through Hangar
```

If 8080 or 8081 is already taken — an identity provider and a second gateway are
the usual culprits — set `HANGAR_PORT` / `UPSTREAM_PORT` and pass the same ports
to the drivers via `--url`.

The relay is live by default; `config.yaml` spells `relay_tasks_enabled` out
anyway so the example still runs against a deployment that turned it off.

Each script exits non-zero on the first failed assertion, so they drop straight
into CI or a release checklist.

To smoke a release candidate, name the image under test. `HANGAR_IMAGE` has no
default — compose errors out rather than guess, because a stale guess passes and
tells you nothing:

```bash
HANGAR_IMAGE=ghcr.io/mcp-hangar/mcp-hangar:2.0.0-rc.3 \
  docker compose -f examples/task_upstream/docker-compose.yml up --build
```

Use the **semver** form of the release tag (`2.0.0-rc.3`), not the PEP 440
project version (`2.0.0rc3`): the image is tagged from the git tag, so the two
never coincide on a prerelease and the PEP 440 spelling 404s on pull.

Without Docker, run the two processes yourself: `python examples/task_upstream/server.py`
(listens on `:8080`) and a Hangar configured with this `config.yaml`.

## What each script proves

**`smoke_upstream.py`** — `tools/call` returns `{"task": {...}}` and not content;
`tasks/get` reaches `completed`; `tasks/result` carries the payload; `tasks/list`
includes it; an unknown id fails closed; the consent tool parks in
`input_required` and `tasks/update` resolves it; `tasks/cancel` cancels. Run this
first when something breaks: it tells an upstream regression apart from a relay
regression.

**`drive_relay.py`** — the same lifecycle *through* Hangar, plus what only the
relay can be asked. It drives the SEP-2663 surface: the tasks extension
advertised on `server/discover` (under `capabilities.extensions`, since
the 2026-07-28 `ServerCapabilities` has no `tasks` field and a server advertising it
there has the entry sieved out) and naming only the methods actually served; a
handle that survives relaying; `tasks/get` polling to `completed` with the
outcome carried **inline**; SEP-2663 field names on the wire (`ttlMs`,
`pollIntervalMs`, `resultType`, flat — not `ttl` / `pollInterval` / nested
`task`); `tasks/result` and `tasks/list` actually answering `-32601`; an unknown
id yielding no enumeration side channel; `tasks/cancel` acknowledging *empty*,
because cancellation is cooperative and the ack must not claim an outcome the
upstream never reported; a paused task exposing its `inputRequests` and resolving
through the governed `tasks/update`; and a client that never declared
`io.modelcontextprotocol/tasks` being refused `-32021` with a payload naming what
to declare.

It speaks through the SDK's `Client` with an explicit `mode="2026-07-28"`, and
both parts are forced rather than stylistic. SEP-2663 requires `Mcp-Name:
<taskId>` on every `tasks/*`; that header varies per request and the transport
only takes connection-level headers, so `_session.py` declares typed requests
carrying `name_param` and lets the SDK stamp it. And the `initialize` handshake
cannot negotiate 2026-07-28 at all — `HANDSHAKE_PROTOCOL_VERSIONS` tops out at
`2025-11-25` — so a plain `ClientSession` lands on a legacy connection where
these methods correctly do not exist.

Results are read as raw dicts on purpose. Validating them against Hangar's own
`tasks_wire` models would make the smoke test circular: it would prove our models
parse our own output. Asserting on literal wire keys is the only version that can
catch us serving the wrong shape.

There is no interactive-consent driver any more. Hangar used to prompt the client
with `elicitation/create` in-handler during the `tasks/get` that observed a pause;
that belonged to the 2025-11-25 wire, which Hangar no longer serves. On the
SEP-2663 wire the client resolves its own input by driving `tasks/update`, which
`drive_relay.py` covers. Consent is still governed and still fail-closed — gated
on the update rather than on a prompt.

In egress topology a client never calls the upstream tool directly — it calls
Hangar's `hangar_call` meta-tool, so the task handle arrives nested in a batch
envelope. `_session.py::task_id_from_hangar_call` digs it out.

## Why the server hand-rolls so much

`mcp==2.0.0b2` ships the Tasks *types* and the negotiated-extension plumbing, but
no server-side task store and no `tools/call` → task path. The high-level
`MCPServer` only returns `CallToolResult` / `InputRequiredResult`, and the runner
serializes `tools/call` strictly as `CallToolResult`, so a raw `{"task": {...}}`
body fails validation. Hence two workarounds, both documented in `server.py`:

1. a server middleware short-circuits `tools/call` for the task tools and returns
   `CreateTaskResult` *before* the runner's spec-serialize sieve;
2. the `tasks/*` methods are registered via `add_request_handler` — they are not
   spec client methods on b2, so their result shape is returned raw.

Expect this file to shrink as the SDK lands its server-side Tasks surface.

### Two gotchas worth knowing

**Params are validated by alias only.** The SDK's `GetTaskRequestParams` aliases
`task_id` → `taskId`, so it accepts **only** camelCase. A native v2 client sends
camelCase; Hangar's relay forwards snake_case. The server therefore validates
against local models whose `AliasChoices` accept both — otherwise one of the two
callers gets `-32602` and it looks like a relay bug.

**The pin is exact for a reason.** SEP-2663 reshapes Tasks *within* the v2 line:
`tasks/list` is removed and `tasks/update` added. `drive_relay.py` already treats
a `-32601` on `tasks/list` as the forward-compat path rather than a failure. When
bumping the pin, re-run `smoke_upstream.py` first — it is the contract check.

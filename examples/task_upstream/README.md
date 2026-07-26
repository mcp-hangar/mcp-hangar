# Task-emitting upstream (MCP SDK v2, Tasks extension)

A small MCP server that answers a tool call with a **task handle** instead of a
result, and serves the native `tasks/*` lifecycle. It exists to exercise
mcp-hangar's governed task relay (ADR-014) end to end — including the
human-in-the-loop consent gate — against a real upstream rather than a mock.

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
| `consent_hitl.py` | answers Hangar's mid-flight consent prompt (accept / decline) |

## Run it

```bash
docker compose -f examples/task_upstream/docker-compose.yml up --build

python examples/task_upstream/smoke_upstream.py --url http://127.0.0.1:8081/mcp  # upstream alone
python examples/task_upstream/drive_relay.py                                     # through Hangar
python examples/task_upstream/consent_hitl.py --decision accept
python examples/task_upstream/consent_hitl.py --decision decline
```

Each script exits non-zero on the first failed assertion, so they drop straight
into CI or a release checklist.

To smoke a release candidate, point the compose file at the image under test:

```bash
HANGAR_IMAGE=ghcr.io/mcp-hangar/mcp-hangar:2.0.0-rc.1 \
  docker compose -f examples/task_upstream/docker-compose.yml up --build
```

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
relay can be asked: the `tasks` capability advertised at `initialize`, a handle
that survives relaying (rather than `TaskRelayNotSupported`), the tool digest
re-verified when the result is retrieved, `tasks/list` scoped to its owner, and
an unknown id yielding no enumeration side channel.

**`consent_hitl.py`** — the interactive gate. The upstream parks the task; Hangar
sends `elicitation/create` to the client *in-handler* during the `tasks/get` that
observes the pause; the gate opens only on `action == "accept"`. Both decisions
are passing runs — `accept` must complete the task, `decline` must fail it
**closed**. A client that never negotiates the `elicitation` capability is the
third case: Hangar has nobody to ask, so it fails closed. (This is not the
synchronous L7 `requireApproval` mechanism, which is non-interactive by design.)

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

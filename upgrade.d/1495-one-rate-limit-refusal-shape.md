### `error_type` names the failure in every MCP tool error payload

A tool error payload used to name the failure under `type`, while a
`hangar_call` result named it under `error_type`. Both now use **`error_type`**,
so a client reads one key whichever tool answered.

```json
{"error": "...", "error_type": "RateLimitExceeded", "details": {}}
```

**A client that matched `type` on a tool error payload has to read `error_type`
instead.** The `error` and `details` keys are unchanged. This applies to every
tool error, not only a rate-limit refusal. A `hangar_call` result already used
`error_type` and does not change.

A rate-limit refusal also had more than one shape, depending on which limiter
refused the call:

- The tool wrapper's own check (`hangar_load`, `hangar_fetch_continuation` and
  the other tools charged by name) raised out of the wrapper as an **MCP
  error**, so the call came back as a JSON-RPC error rather than a tool result.
  It is now the same error payload every other path returns. **A client that
  caught a JSON-RPC error to detect this refusal now sees a normal tool result
  whose `error_type` is `RateLimitExceeded`.**
- A refusal raised inside a tool body -- by the command bus, or by `charge_tool`
  for work a tool does itself -- was already a payload, and keeps being one.

`details` now carries the refusal's own fields on every path, where before a
tool payload dropped them and left only the message text:

```json
{
  "error": "RateLimitExceeded: this caller's rate limit for hangar_load is used up (2 at once, refilled at 5 per second). Retry after 0.20s.",
  "error_type": "RateLimitExceeded",
  "details": {
    "limit": 2,
    "window_seconds": 1,
    "retry_after": 0.2,
    "scope": "caller",
    "key": "hangar_load",
    "rps": 5
  }
}
```

`scope` is `caller` when the caller's own budget was used up and `all_callers`
when the shared one was, as it already was over the HTTP API. Only a rate-limit
refusal carries `details`; every other tool error still has `details: {}`.

**Security log.** A refusal by the command bus's limiter now reaches the
security handler, which recorded only the tool-level ones before. Each refusal
is recorded once, as `rate_limit_exceeded`, and its details gained `scope`, the
`key_kind` (`tool` or `command`) and the `key` it names. These are values Hangar
chose: no argument value and no caller's own text is recorded. A refusal is no
longer also recorded as a `validation_failed` event.

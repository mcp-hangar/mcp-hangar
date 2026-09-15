### each caller can have its own command-bus rate limit

`rate_limit` keeps its meaning: `rps` and `burst` are the budget all callers
share, one per command type. Without the new key below, it admits and refuses
what it did before, except for the read-only tools. **Nothing needs changing.**
What is new:

- **`rate_limit.per_caller`** gives each caller a budget of its own, per
  command type, under the shared one. A caller is its tenant and its principal.
  Callers with neither are one caller and share one budget: anonymous callers,
  every caller when authentication is off, and work no request started. A
  caller that has used up its own budget is refused without spending the
  shared one, so the other callers keep theirs. Both keys are required, and a
  malformed `per_caller` stops startup. It is read at startup and counted per
  replica, as `rate_limit` is. Set it below the shared budget: at or above it,
  it never refuses a call the shared budget would not.
- **The read-only tools are never refused** by the rate limit: `hangar_list`,
  `hangar_status`, `hangar_details`, `hangar_group_list`, `hangar_health`,
  `hangar_metrics`, `hangar_discovered` and `hangar_quarantine`. They read
  Hangar's own state and call nothing outside it. `hangar_tools` is still
  limited, because it may start a stopped server. So is `hangar_sources`,
  because it runs every discovery source's health check, and the Kubernetes
  source's check is a call to the cluster's API.
- **A refusal reads the same on every path.** Its message names the code, the
  budget, the limit and when to retry, as in the example below. A `hangar_call`
  result has `error_type` `RateLimitExceeded`. The HTTP API answers `429` with
  a `Retry-After` header, and its `details` hold `retry_after` and `scope`
  (`caller` or `all_callers`). A client or an alert that matched the old text,
  `Rate limit exceeded: N requests per Ms`, needs the new one.

```yaml
rate_limit:
  rps: 50         # all callers together, per command type
  burst: 100
  per_caller:     # each caller, per command type
    rps: 5
    burst: 10
```

```text
RateLimitExceeded: this caller's rate limit for InvokeToolCommand is used up (10 at once, refilled at 5 per second). Retry after 0.20s.
```

`per_caller` is a key of its own so that no deployment admits more than it
did. Making `rate_limit` itself per caller would have multiplied what a gateway
admits in total by its number of callers, without the operator asking for it.

### a group member is judged by its own health

A call through a group used to count against the member that took it whenever
the call did not succeed. It now counts only when the outcome is evidence the
member is unwell. **Nothing to change in configuration.** What you may see:

- Fewer members leave rotation, and a group's circuit opens less often.
  `mcp_hangar_group_circuit_open` is at `1` less often, and `hangar_group_list`
  shows `consecutive_failures` at `0` for a member that only answered bad
  requests.
- **A member that answered the request counts as healthy**, as a success does:
  a tool result with `isError: true`, a JSON-RPC error `-32602` (invalid
  params) or `-32601` (method not found), and a JSON-RPC error whose code is
  outside the reserved range `-32768` to `-32000`, such as a tool's own `-1`.
- **A refusal Hangar makes before asking the member counts as nothing**: the
  command bus's `rate_limit`, an egress rule, or a tool the member does not
  list. Refusals by Hangar's gates (tool access, pins, validators, approval, the
  tenant's budget) already counted as nothing.
- **These still count against the member, as before**: a transport failure, a
  timeout, a failed start, the member's own backoff or dead state, and a
  JSON-RPC error in the reserved range, such as `-32603` (internal error),
  `-32700` (parse error) or `-32000` (server error). An HTTP upstream's `4xx`
  or `5xx` status reaches the group as `-32000`, so it counts.
- An alert that fired when callers sent bad requests through a group no longer
  fires for them. Alert on the tool errors themselves instead.

**core:** a group member is judged by its own health. Every call through a
group that did not succeed used to count against the member that took it. So
errors the caller caused took a healthy member out of rotation, and enough of
them opened the group's circuit: a division by zero, arguments the tool rejects,
a tool result marked `isError: true`, or a call the command bus's rate limit
refused before it reached the member.

A member that answered the request now counts as healthy. That covers a tool
result with `isError: true`, invalid params (`-32602`), method not found
(`-32601`), and a JSON-RPC error whose code is outside the reserved range
`-32768` to `-32000`, such as a tool's own `-1`. A refusal Hangar makes before
asking the member counts as nothing: its rate limit, an egress rule, or a tool
the member does not list. A transport failure, a timeout, a failed start, the
member's own backoff, and a JSON-RPC error in the reserved range, such as
`-32603` or `-32000`, still count, as before.

Operators may see fewer members leave rotation and fewer circuit openings, with
`mcp_hangar_group_circuit_open` at `1` less often. See `UPGRADE.md`.

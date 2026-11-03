### Approvals over stdio now end after 50 seconds by default

On a gateway serving stdio, an approval stays open for the shorter of `approval_timeout_seconds` and the new `approvals.stdio_wait_seconds`, which defaults to 50. A human slower than that cannot get the call through: the approve is refused and a retried call opens a new approval. If your client waits longer than the MCP TypeScript SDK's default of 60 seconds, raise `approvals.stdio_wait_seconds` to fit it. A gateway serving HTTP is unchanged.

### a bounded HTTP stop abandons the calls still running when the bound runs out

`http.graceful_shutdown_timeout_s` used to bound nothing in practice. On a stop, every MCP call in flight lost its stream at once, so the client saw a transport error such as `peer closed connection without sending complete message body`, and the call ran on upstream to completion, however long that took. The process stayed up until then too.

Now the stop waits for those calls and delivers their responses. Without a bound it waits as long as they take, as before, but now the client gets its result. With a bound, the tool calls still running when the bound runs out (`hangar_call`, and the front door's tool calls) are abandoned. Each caller gets a JSON-RPC error on its own stream instead of a transport error:

```json
{"jsonrpc": "2.0", "id": 7, "error": {"code": -32000, "message": "The gateway is shutting down and abandoned this call before it finished; it may or may not have taken effect upstream", "data": {"reason": "shutdown", "batch_id": "..."}}}
```

Other requests still running at the bound (`hangar_start`, `hangar_warm`, a cold `hangar_tools`, a resource read) get no answer; their connection ends, as before. After that the upstream servers are stopped and the process exits, so an abandoned call may never finish upstream. Before, it always ran to completion.

What to check:

- A client that retries on transport errors only does not see this error as retryable. Treat `error.data.reason == "shutdown"` as "the gateway went away; the call may or may not have run".
- If calls must be allowed to finish, set the bound above your longest call, or leave it unset. In Kubernetes, keep the pod's `terminationGracePeriodSeconds` above the bound plus a few seconds for the component shutdown, so the kubelet's SIGKILL does not land first.

### the HTTP graceful-shutdown bound can be set

`serve --http` reads a new key, `http.graceful_shutdown_timeout_s`. It is how
many seconds a stop waits for the requests already in flight before it cancels
them.

```yaml
http:
  graceful_shutdown_timeout_s: 90
```

Nothing changes unless you set it. Unset, Hangar passes uvicorn its own default,
`None`, which waits for in-flight requests without a bound. The process then
ends when they finish, or when something kills it. In Kubernetes that is the
kubelet's SIGKILL at the end of the pod's `terminationGracePeriodSeconds`, 30
seconds by default.

- The value is a positive whole number of seconds. Any other value, or an
  `http` that is not a mapping, refuses to start. A reload with such a value is
  refused too, and everything keeps running as it was.
- The bound is read when the HTTP server starts. A reload checks it, but the
  running server keeps the bound it started with. Restart to change it.
- Stdio mode has no HTTP server, and ignores the key.
- `starting_http_server` logs the bound in force as
  `graceful_shutdown_timeout_s`, and logs `null` when it is unset.

**In Kubernetes**, the bound only helps if the pod lives long enough to use it.
The kubelet counts the grace period from the start of the `preStop` hook, so set
`terminationGracePeriodSeconds` longer than the `preStop` delay plus the bound,
with room for Hangar's own cleanup after it. The mcp-hangar Helm chart sets all
three from its `shutdown` values, and refuses to render a grace period that is
too short.

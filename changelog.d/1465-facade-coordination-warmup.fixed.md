**core:** `Hangar.start()` and `SyncHangar.start()` now start the coordination
and front-door warm-up that `serve` starts. Under a `coordination:` block, an
embedded gateway now takes and renews the management lease and follows the
shared event log. `stop()` stops the tailer and releases the lease last. In
front-door mode, `start()` now warms the catalogue: it starts every configured
server on a thread of its own, and runs the required-catalogue retry when
`tool_access.required_catalogue` is set. Before, an embedded front door listed
nothing until each server had been started some other way. `stop()` stops the
retry and waits up to 10 s for the warm-up. Concurrent `start()` calls now share
one bootstrap, and a `stop()` made during a `start()` waits for it. Before, two
concurrent starts could both bootstrap, and the first context was never
stopped.

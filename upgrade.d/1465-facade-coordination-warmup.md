### the Python facade takes the management lease and warms a front door

`Hangar.start()` and `SyncHangar.start()` now start two things that `serve`
starts and the facade did not:

- **Under a `coordination:` block**, the management lease keeper and the event
  tailer. An embedded gateway now takes and renews the lease, and follows the
  shared event log, as a served replica does. `stop()` stops the tailer, then
  shuts down, then releases the lease.
- **In front-door mode** (`tool_access.mode: front_door`), the catalogue
  warm-up. Every configured server is started at `start()`, on a thread of its
  own, so `start()` does not wait for it. With `tool_access.required_catalogue`
  set, the required-catalogue retry runs after it. `stop()` stops the retry and
  waits up to 10 s for the warm-up to finish.

In egress mode, and without a `coordination:` block, nothing changes.

**What to check.** An embedded front door now starts every configured server
when it starts, not on first use, as `serve` does. `stop()` can take up to 10 s
longer while a warm-up is still starting servers.

**Concurrent starts.** Concurrent `start()` calls now share one bootstrap: the
later calls return once the first has finished. A `stop()` made while a
`start()` is in flight waits for it, then stops everything it started.
`SyncHangar.start()` and `SyncHangar.stop()` are serialised across threads in
the same way. Before, two concurrent starts could both bootstrap, and the
first context was never stopped.

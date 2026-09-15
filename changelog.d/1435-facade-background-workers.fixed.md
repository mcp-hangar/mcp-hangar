**core:** the Python facade now runs the background workers `serve` runs.
`Hangar.start()` and `SyncHangar.start()`, and their `async with` and `with`
blocks, start the GC, health-check and metrics snapshot workers, and the config
reload worker for a config file, through the function `ServerLifecycle.start`
uses, so the two start the same set. None of them ran under the facade before:
a server idle past its `idle_ttl_s` kept running for the life of the host, and
no server was health checked on schedule. `stop()` stops the workers and waits
for their threads to end, up to 10 seconds in total, and a second `start()` or
`stop()` does nothing. A stopped GC or health-check worker, or config reload
poller, now ends its thread at once rather than after its interval, and
shutdown waits for them, under `serve` too. See `UPGRADE.md` for what an
embedded gateway now does that it did not.

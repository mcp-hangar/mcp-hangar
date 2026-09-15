### the Python facade stops idle servers and health-checks them

`Hangar` and `SyncHangar` now start the background workers `mcp-hangar serve`
starts, and stop them on `stop()` or at the end of the `async with` or `with`
block. No code change is needed, but an embedded gateway now does what a served
one does:

- A server idle for longer than its `idle_ttl_s` is stopped by the GC worker,
  which runs every 30 seconds. `HangarConfig.add_mcp_server()` defaults
  `idle_ttl_s` to 300. The next call starts the server again, and pays its
  start-up time. To keep a server running for the life of the host, give it a
  larger `idle_ttl_s`, up to 86400.
- Every running server is health checked by the health-check worker, every 60
  seconds, so a failing server is noticed, and one that recovers is returned
  to rotation, without a call.
- The metrics snapshot worker records metrics history under `./data`, as it
  does under `serve`.
- A facade started from a config file, `Hangar.from_config()` or
  `SyncHangar.from_config()`, watches that file, and a change to it reloads the
  configuration. To keep the file from being reloaded, set:

  ```yaml
  config_reload:
    enabled: false
  ```

`stop()` now waits for the workers' threads to end, up to 10 seconds in total.

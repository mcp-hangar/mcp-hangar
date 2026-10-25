### On a front door, a reload starts the servers it adds or rebuilds

With `tool_access.mode: front_door`, a configuration reload (`SIGHUP`,
`POST /api/config/reload`, `hangar_reload_config` or the file watcher) now
starts each server it adds and each server whose settings it changed, right
after the reload applies, on a background thread. This is what boot already
does for every configured server, and it is how their tools reach `tools/list`.
Before, they stayed `COLD` and their tools were not served until a restart.

- A reload can therefore launch subprocesses or containers, or open connections
  to remote servers, on the gateway that ran it. A server that fails to start is
  logged as `front_door_reload_warmup_failed` with its error type, and the
  others still start; the reload itself reports success either way, as before.
- Servers the reload keeps unchanged are not restarted.
- `idle_ttl_s` applies to them as to any started server.
- `egress` gateways are unchanged: a reload starts nothing, and servers start on
  first use.
- Nothing to configure. A deployment that relied on reload-added servers staying
  cold on a front door should note that they now start.

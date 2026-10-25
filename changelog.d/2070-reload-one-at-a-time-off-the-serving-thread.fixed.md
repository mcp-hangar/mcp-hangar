**core:** a SIGHUP reload no longer freezes the gateway, and reloads run one
at a time. The SIGHUP handler ran the reload itself, on the main thread that
serves HTTP and stdio, so while a graceful reload waited for calls in flight
(up to 30 s) the gateway answered nothing, not even `/health/live`, and a
second SIGHUP started a reload nested inside the first. The handler now only
asks a reloader thread for a reload, and any number of SIGHUPs that arrive
while one runs ask for exactly one more. Nothing serialised reloads either:
the file watcher, SIGHUP, the `hangar_reload_config` tool and
`POST /api/config/reload` could run at once, compute their diffs before any of
them stopped a server, and stop the same server twice. A reload asked for while
another runs now waits for it, then runs on the configuration it left and
returns its own result; the log says `config_reload_waiting`

**core:** new config key `http.graceful_shutdown_timeout_s`: how many seconds
`serve --http` waits, once told to stop, for the requests already in flight
before it cancels them. Unset keeps today's behaviour, uvicorn's own default,
which waits for them without a bound. The value must be a positive whole number
of seconds. Any other value refuses the configuration, whether it comes from a
file, from `bootstrap(config_dict=...)` or from a reload. The bound is read when
the HTTP server starts, so a reload does not change it for a running server.
`starting_http_server` logs the bound in force. See `UPGRADE.md`

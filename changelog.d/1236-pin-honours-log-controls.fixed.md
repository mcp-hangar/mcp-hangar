**cli:** `mcp-hangar pin` now honours `--quiet`, `MCP_LOG_LEVEL` and the
config file's `logging.level`. It never set up logging, so every stdio-client
`[info]` and `[debug]` line printed around its two or three lines of output and
none of the three controls changed that. It now resolves its log level through
the same function as `serve`, in the same order: `--log-level` (new on `pin`),
else `MCP_LOG_LEVEL`, else `logging.level`, else INFO.

`--quiet`, before or after `pin`, lets through log errors only and drops the
lines that just confirm success ("every pinned tool still matches its pin.",
"pinned <server>: N tool(s)" and the backup note). Digests, drift and servers
that could not be asked always print; so does `--json`. The answer goes to
stdout, and log output and usage errors go to stderr, so
`mcp-hangar pin --check >report 2>/dev/null` keeps the drift report. Usage
errors (no such file, invalid YAML, unknown `--server`) used to print on stdout.
Exit codes are unchanged.

The `watchdog package not installed` debug line no longer prints on every CLI
invocation (`--help` and `pin` included), where it fired at import and no log
level could suppress it. It now logs when config hot reload starts and has to
fall back to polling, which is the only time it applies.

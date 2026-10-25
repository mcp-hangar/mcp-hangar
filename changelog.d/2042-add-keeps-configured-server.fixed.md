**cli:** `mcp-hangar add` no longer replaces a server the config already has. It
wrote the registry entry over the existing one, dropping the server's
`tool_projection` (its digest pins and enforcement), `tools` policy, extra args and
`env` without a word, and with `-y` without asking. Adding a server that is
configured exactly as the registry describes it now changes nothing and exits 0;
one configured differently is refused with exit 1, naming what the replacement
would drop or change. `--replace` keeps the old behaviour and says what it dropped

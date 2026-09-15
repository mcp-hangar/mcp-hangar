### a reload restarts only the servers whose settings changed

A configuration reload used to restart every server whose entry left
`resources` out, which is most of them, even when nothing in the file had
changed. That applied to a reload over `POST /api/config/reload`, SIGHUP or the
file watcher alike. Each reload stopped those servers and dropped their
in-flight calls, and their next call started a new process.

A reload now keeps a server running, with the same process, sessions, health
and circuit state, unless the file changes something the server is built from:
`mode`, `command`, `args`, `image`, `build`, `endpoint`, `env`, `volumes`,
`resources`, `network`, `read_only`, `user`, `description`, `idle_ttl_s`,
`health_check_interval_s`, `max_consecutive_failures`, a list of predefined
`tools`, `auth`, `tls`, `http` or `capabilities`. Defaults count as set: leaving
a setting out and spelling out its default are the same server. A group's
inline members follow the same rule.

What changes for you:

- **A governance change no longer restarts a server.** A reload applies a
  change to a server's `tools` access block, `access`, `tool_access`,
  `tool_projection` or `header_exposure`, or to a member's `weight` or
  `priority` in its group, and the server keeps running. Before, such a change
  restarted the server.
- **`mcp_servers_updated` lists only the servers the reload restarted**, in the
  reload response and in the `ConfigurationReloaded` event. The servers it kept
  are in `mcp_servers_unchanged`.
- **A reload no longer restarts a server whose settings did not change.** To
  restart one, stop it with `hangar_stop` or `POST /api/mcp_servers/{id}/stop`.
  Its next call starts it again.
- A server whose `env`, `description` or intervals were edited over the REST
  API is still restarted with the file's values, as before.

### Bind existing pins to their upstream

Pins you already have keep working and check the tool definition only. To have them also refuse a server that points at a different upstream, run `mcp-hangar pin --write` once: it adds `tool_projection.upstream` to each pinned server. A subprocess server's identity includes a hash of its command line, so changing the command (a new package version in `npx` arguments, for example) is a new upstream and the pins need `pin --write` again. A remote server's identity is its origin (scheme, host, port), so a path change does not matter.

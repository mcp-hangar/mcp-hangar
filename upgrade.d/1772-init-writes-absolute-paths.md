### `init` writes absolute paths; state follows a configured `event_store.path`

When `event_store.path` is absolute, the saga state file (`saga_state.db`) and the approvals database (`mcp_hangar.db`) now live in the same directory as the event store instead of in `data/` under the working directory. Configurations without an absolute `event_store.path` are unchanged.

Entries written by an earlier `mcp-hangar init` use a bare `mcp-hangar` command and a relative `data/` path, which fails when the client spawns Hangar with a minimal `PATH` or an unwritable working directory. Rerun `mcp-hangar init` to refresh them. If you move to the new location, copy any existing `data/*.db` files next to the new `event_store.path` first.

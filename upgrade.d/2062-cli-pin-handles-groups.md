### `mcp-hangar pin --check` now checks the pins declared on a group

A configuration with a `mode: group` entry used to make every `pin` run exit 2,
so a CI gate on `pin --check` either failed on every run or was narrowed with
`--server`. It now exits 0, 1 or 2 as for servers alone, and the pins a group
declares under its own `tool_projection.pins` are compared for the first time:
each member that serves a pinned tool must serve it at the pin, and a pinned
tool no member serves is drift.

- A group pin that went stale while it was never checked now fails the gate
  with exit 1, reported as `drift <group>.<tool> (member <id>)`. Refresh it by
  hand: `pin --write` does not write pins into a group, because a group's pin
  holds for every member and is the operator's to choose.
- `--server <group>` now asks that group's members and checks its pins.
  `--server <member>` checks that member's own pins, not those of the groups it
  belongs to.
- `pin --check --json` adds a `member` key to a drift entry that comes from a
  group pin. Entries for servers are unchanged.

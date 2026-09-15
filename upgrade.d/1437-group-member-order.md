### a group member is its top-level server, whatever the order in the file

A group member whose `id` names a top-level server in `mcp_servers` is now that
server, wherever the group appears in the file, at startup and on a reload.
Before, a group listed above the server got a member built from the member
entry alone. With only an `id`, that was a server with no command, which could
not start, and not the server the rest of Hangar knew by that id.

Two configurations now load differently:

- **A member that names no server is refused.** A member whose id is not a
  top-level server, and whose entry does not say how to run one, fails the
  load: `Group 'pool' member 'm1' names no server`. Before, it loaded and
  failed only when a call was routed to it. A reload with such a member is
  refused and changes nothing. Declare the server under `mcp_servers`, or give
  the member entry what its mode needs: `command` for `subprocess`, `image` or
  `build` for `docker`, `endpoint` for `remote`.
- **A member entry cannot redefine a top-level server.** When a member entry
  sets server fields such as `command`, `env` or `mode`, and a top-level server
  has the same id, the member is the top-level server and the entry's server
  fields are ignored. Before, which of the two the group held depended on the
  order in the file. The ignored fields are named in a
  `group_member_entry_settings_ignored` warning. `weight`, `priority` and
  `tools` on a member entry still apply.

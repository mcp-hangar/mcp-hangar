**core:** a group member that names a top-level server is now that server,
whatever the order in `mcp_servers`. A group listed before its member's server
built the member from the member entry alone: with only an `id`, a server with
no command that could not start, and not the one the repository held under that
id. A member entry that defined the server inline, under the id of a top-level
server declared later, was a second copy the repository never held, so a reload
never stopped it. Every top-level server is now built before any group, at
startup and on a reload. A member whose id names no server, and whose entry does
not say how to run one, now fails the load and names the group and the member.
Server settings on a member entry that names a declared server are ignored, as
they were when the group came after the server, and a
`group_member_entry_settings_ignored` warning now names them. See `UPGRADE.md`.

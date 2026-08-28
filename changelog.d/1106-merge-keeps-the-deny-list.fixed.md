**core:** merging two tool-access scopes could widen access. A tool denied at
the broader scope came back **allowed** as soon as a narrower scope declared an
allow list -- for example a server-level `allow: ["*"], deny: ["drop_*"]`
merged with a group-level `allow: ["*"]` allowed `drop_db`.

`merge()` dispatched on which lists were populated, and each branch rebuilt a
piece of the deny > approval > allow > default ladder out of the lists its own
condition named, dropping the rest. The "both sides have an allow_list" branch
consulted neither `deny_list`; the two mirrored branches each dropped one side's
`deny_list`. This broke the invariant `merge` documents about itself --
`merged.filter_tools(tools) == narrower.filter_tools(broader.filter_tools(tools))`.

Merging now composes the two policies' own `is_tool_allowed` answers, so the
precedence ladder exists in one place and a merge can only ever remove tools.

**Check your effective policy after upgrading.** A merged policy that has been
silently wider than intended will narrow to what it always said it was, and a
tool that has been callable may stop being callable -- which is the correct
behaviour, and may still be a surprise. `hangar tools list` for the affected
identity shows the effective set.

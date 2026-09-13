**core:** the front door now measures the surface it projects. It records how
many bytes of tool definitions a `tools/list` hands a client, how many of them
each upstream contributes, and how often a caller is served a different list
than before. There are three new metrics.
`mcp_hangar_projected_surface_bytes{kind}` is a histogram, split
`governed`/`management` like `mcp_hangar_projected_tools`.
`mcp_hangar_projected_upstream_bytes{mcp_server}` is a histogram of one
upstream's share of a listing, with a group counted under its group id.
`mcp_hangar_projection_changes_total` is a counter of listings whose projection
differed from the one the same caller was last served on that replica. So
`increase(mcp_hangar_projection_changes_total[1h]) > 0` answers "did the fleet
change under a connected client". None of them has a tenant label. Only
listings the client received are measured: the SDK's own listing before a
`tools/call` is not.
